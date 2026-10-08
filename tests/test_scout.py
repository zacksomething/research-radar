import copy
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_radar import scout


def bundle_fixture():
    return {
        "schema_version": 1,
        "run": {"id": "test-run-20260919", "status": "complete",
                "window": {"start": "2026-09-18", "end": "2026-09-19"}, "sources": []},
        "papers": [
            {"id": "arxiv:synthetic-001", "title": "Synthetic paper, not a real finding",
             "abstract": "Synthetic fixture only", "authors": [{"name": "Fixture Researcher", "affiliations": []}],
             "url": "https://example.org/paper", "pdf_url": "https://example.org/paper.pdf",
             "published": "2026-09-18", "updated": "2026-09-18", "sources": ["fixture"],
             "hf_upvotes": 0, "clusters": ["Test"], "primary_cluster": "Test", "prior": -2.0,
             "prior_signals": [], "change": "new"},
        ],
    }


def complete_candidate(review, known_company=False, stage="seed", confidence="high"):
    candidate = review["candidates"][0]
    paper_id, person_id, company_id = candidate["paper_id"], "synthetic:person:001", "synthetic:company:001"
    candidate["review_status"] = "complete"
    candidate["triage"] = {"status": "reviewed", "relevant": True, "abstract_score": 3,
                            "reason": "Synthetic relevance assessment"}
    candidate["person"] = {"id": person_id, "name": "Synthetic Researcher", "status": "verified",
                            "role": "co-first author", "affiliation": "Synthetic Lab", "evidence_ids": ["person"]}
    review["evidence"] = [
        {"id": "paper", "url": "https://example.org/paper.pdf", "checked_at": "2026-09-19T08:00:00Z",
         "source_date": None, "kind": "paper_full_text", "entity_ids": [paper_id],
         "note": "Synthetic full text fixture; not a real evaluation"},
        {"id": "person", "url": "https://example.org/researcher", "checked_at": "2026-09-19",
         "source_date": "2026-09-18", "kind": "author_homepage", "entity_ids": [person_id, paper_id],
         "note": "Synthetic identity and professional location fixture"},
    ]
    for name, assessment in candidate["assessment"].items():
        assessment.update(score=4, reason="Synthetic scored assessment",
                          evidence_ids=["person" if name in {"team", "reachability"} else "paper"])
    candidate["assessment"]["technical"]["scope"] = "full_text"
    if known_company:
        candidate["company"].update(id=company_id, name="Synthetic Company", status="verified", evidence_ids=["company"])
        candidate["relationship"].update(status="verified", type="founder", person_id=person_id,
                                         company_id=company_id, evidence_ids=["company"])
        candidate["financing"].update(status="verified", stage=stage, confidence=confidence,
                                      company_id=company_id, evidence_ids=["funding"])
        review["evidence"].extend([
            {"id": "company", "url": "https://example.org/company", "checked_at": "2026-09-19",
             "source_date": None, "kind": "company", "entity_ids": [person_id, company_id],
             "note": "Synthetic company-founder link"},
            {"id": "funding", "url": "https://example.org/funding", "checked_at": "2026-09-19",
             "source_date": "2026-09-17", "kind": "funding_announcement", "entity_ids": [company_id],
             "note": "Synthetic financing event"},
        ])
    return candidate


class ScoutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.papers = self.root / "papers.json"
        self.review_path = self.root / "review.json"
        self.bundle = bundle_fixture()
        self.save(self.papers, self.bundle)

    def save(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def prepared(self, limit=None):
        review = scout.prepare(self.papers, self.review_path, limit, overwrite=True)
        review["as_of"] = "2026-09-19"
        return review

    def rendered(self, review):
        self.save(self.review_path, review)
        result = scout.render(self.review_path, self.root / "data")
        audit = json.loads(Path(result["evidence"]).read_text(encoding="utf-8"))
        return result, audit

    def test_prepare_preserves_complete_bundle_and_does_not_prior_filter(self):
        other = copy.deepcopy(self.bundle["papers"][0])
        other.update(id="arxiv:synthetic-002", prior=100)
        self.bundle["papers"].append(other)
        self.save(self.papers, self.bundle)
        review = self.prepared()
        self.assertEqual(review["paper_bundle"], self.bundle)
        self.assertEqual([c["paper_id"] for c in review["candidates"]], [p["id"] for p in self.bundle["papers"]])
        limited = self.prepared(limit=1)
        self.assertEqual(limited["selection"]["omitted_paper_ids"], [other["id"]])
        self.assertEqual(limited["paper_bundle"], self.bundle)

    def test_unfilled_review_stays_needs_review_without_scores_or_profiles(self):
        result, audit = self.rendered(self.prepared())
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["profiles"], [])
        self.assertIsNone(audit["results"][0]["score"])
        self.assertIn("needs_review", Path(result["report"]).read_text(encoding="utf-8"))

    def test_unknown_stage_scores_with_uncertainty_and_no_bonus(self):
        review = self.prepared()
        candidate = complete_candidate(review)
        candidate["visibility"].update(status="verified", value="low", reason="Synthetic positive visibility review", evidence_ids=["person"])
        result, audit = self.rendered(review)
        evaluated = audit["results"][0]
        self.assertEqual(result["status"], "complete")
        self.assertEqual(evaluated["score"], 6.0)
        self.assertEqual(evaluated["components"]["visibility_bonus"], 0)
        self.assertEqual(evaluated["components"]["effective_stage"], "unknown")

    def test_confirmed_early_company_is_deterministic(self):
        review = self.prepared()
        candidate = complete_candidate(review, known_company=True)
        candidate["visibility"].update(status="verified", value="low", reason="Synthetic bounded review", evidence_ids=["company"])
        _, audit = self.rendered(review)
        self.assertEqual(audit["results"][0]["score"], 9.6)

    def test_verified_fact_without_evidence_fails(self):
        review = self.prepared()
        candidate = complete_candidate(review)
        candidate["person"]["evidence_ids"] = []
        with self.assertRaisesRegex(ValueError, "requires evidence"):
            self.rendered(review)

    def test_wrong_company_financing_fails_instead_of_dropping(self):
        review = self.prepared()
        candidate = complete_candidate(review, known_company=True, stage="c_plus")
        candidate["financing"]["company_id"] = "synthetic:wrong-company"
        with self.assertRaisesRegex(ValueError, "company identifier"):
            self.rendered(review)

    def test_stage_hard_drop_requires_link_and_medium_or_high_confidence(self):
        review = self.prepared()
        candidate = complete_candidate(review, known_company=True, stage="c_plus")
        _, audit = self.rendered(review)
        self.assertEqual(audit["results"][0]["status"], "excluded_stage")
        candidate["financing"]["confidence"] = "low"
        _, audit = self.rendered(review)
        self.assertEqual(audit["results"][0]["status"], "scored")
        self.assertEqual(audit["results"][0]["score"], 6)
        candidate["financing"]["confidence"] = "high"
        candidate["relationship"].update(status="unknown", evidence_ids=[], person_id=None, company_id=None, type=None)
        _, audit = self.rendered(review)
        self.assertEqual(audit["results"][0]["status"], "scored")
        self.assertEqual(audit["results"][0]["components"]["effective_stage"], "unknown")

    def test_undated_or_old_financing_never_hard_drops(self):
        for date in (None, "2020-01-01"):
            with self.subTest(date=date):
                review = self.prepared()
                complete_candidate(review, known_company=True, stage="c_plus")
                review["evidence"][-1]["source_date"] = date
                _, audit = self.rendered(review)
                result = audit["results"][0]
                self.assertEqual(result["status"], "scored")
                self.assertEqual(result["components"]["effective_stage"], "unknown")
                self.assertFalse(result["financing_freshness"]["usable"])

    def test_explicitly_stale_financing_retains_history_but_does_not_drop(self):
        review = self.prepared()
        candidate = complete_candidate(review, known_company=True, stage="c_plus")
        candidate["financing"].update(status="stale", reason="Funding record requires a fresh check")
        _, audit = self.rendered(review)
        self.assertEqual(audit["results"][0]["status"], "scored")
        self.assertEqual(audit["results"][0]["candidate"]["financing"]["stage"], "c_plus")
        self.assertEqual(audit["results"][0]["components"]["effective_stage"], "unknown")

    def test_employer_or_collaborator_financing_is_not_the_candidates_stage(self):
        for relation in ("employee", "advisor", "research_collaborator"):
            for stage in ("seed", "c_plus", "public"):
                with self.subTest(relation=relation, stage=stage):
                    review = self.prepared()
                    candidate = complete_candidate(review, known_company=True, stage=stage)
                    candidate["relationship"]["type"] = relation
                    candidate["visibility"].update(status="verified", value="low", reason="Synthetic bounded review", evidence_ids=["company"])
                    _, audit = self.rendered(review)
                    result = audit["results"][0]
                    self.assertEqual(result["status"], "scored")
                    self.assertEqual(result["components"]["effective_stage"], "unknown")
                    self.assertEqual(result["components"]["visibility_bonus"], 0)
                    self.assertEqual(result["score"], 6)
                    self.assertNotIn("Financing was explicitly marked stale", result["reasons"])

    def test_old_checked_date_cannot_be_refreshed_by_only_changing_source_date(self):
        review = self.prepared()
        complete_candidate(review, known_company=True, stage="c_plus")
        review["evidence"][-1]["checked_at"] = "2020-01-01"
        with self.assertRaisesRegex(ValueError, "source_date is after checked_at"):
            self.rendered(review)

    def test_financing_snapshot_is_stable_and_backwards_compatible(self):
        review = self.prepared()
        complete_candidate(review, known_company=True, stage="c_plus")
        del review["as_of"]
        _, audit = self.rendered(review)
        self.assertEqual(audit["as_of"], "2026-09-19")
        self.assertEqual(audit["results"][0]["status"], "excluded_stage")

    def test_missing_work_cannot_be_completed_by_flag_alone(self):
        review = self.prepared()
        candidate = complete_candidate(review)
        candidate["assessment"]["team"]["score"] = None
        _, audit = self.rendered(review)
        self.assertEqual(audit["results"][0]["status"], "needs_review")
        self.assertIsNone(audit["results"][0]["score"])

    def test_abstract_only_cannot_stand_in_for_technical_review(self):
        review = self.prepared()
        candidate = complete_candidate(review)
        candidate["assessment"]["technical"]["scope"] = "abstract_only"
        with self.assertRaisesRegex(ValueError, "full_text scope"):
            self.rendered(review)

    def test_evidence_kind_and_entity_binding_are_checked(self):
        review = self.prepared()
        complete_candidate(review, known_company=True)
        review["evidence"][-1]["entity_ids"] = ["wrong-company"]
        with self.assertRaisesRegex(ValueError, "bound to entity"):
            self.rendered(review)

    def test_missing_checked_at_is_rejected(self):
        review = self.prepared()
        complete_candidate(review)
        del review["evidence"][0]["checked_at"]
        with self.assertRaisesRegex(ValueError, "checked_at"):
            self.rendered(review)

    def test_unverified_stage_cannot_claim_unfunded_or_mature(self):
        review = self.prepared()
        candidate = complete_candidate(review)
        candidate["financing"]["stage"] = "c_plus"
        with self.assertRaisesRegex(ValueError, "unverified financing"):
            self.rendered(review)

    def test_failed_lookup_needs_a_record_of_the_source_attempt(self):
        review = self.prepared()
        candidate = complete_candidate(review)
        candidate["company"].update(status="inaccessible", reason="Login required")
        with self.assertRaisesRegex(ValueError, "requires evidence"):
            self.rendered(review)
        review["evidence"].append({"id": "attempt", "url": "https://example.org/directory",
                                   "checked_at": "2026-09-19", "source_date": None,
                                   "kind": "database", "entity_ids": [], "note": "Synthetic login wall"})
        candidate["company"]["evidence_ids"] = ["attempt"]
        _, audit = self.rendered(review)
        self.assertEqual(audit["results"][0]["components"]["effective_stage"], "unknown")

    def test_repeat_run_does_not_touch_manual_notes(self):
        review = self.prepared()
        complete_candidate(review)
        notes = self.root / "data" / "Researchers" / "Synthetic Researcher.md"
        notes.parent.mkdir(parents=True)
        notes.write_text("Manually authored notes", encoding="utf-8")
        first, _ = self.rendered(review)
        before = Path(first["report"]).read_bytes()
        second, _ = self.rendered(review)
        self.assertEqual(first, second)
        self.assertEqual(before, Path(second["report"]).read_bytes())
        self.assertEqual(notes.read_text(encoding="utf-8"), "Manually authored notes")
        self.assertTrue(all("/generated/" in path for path in second["profiles"]))

    def test_prepare_preserves_existing_completed_review_unless_overwrite_is_explicit(self):
        review = self.prepared()
        complete_candidate(review)
        review["candidates"][0]["notes"] = ["Manual research must survive accidental preparation"]
        self.save(self.review_path, review)
        previous = self.review_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists.*--overwrite"):
            scout.prepare(self.papers, self.review_path)
        self.assertEqual(self.review_path.read_bytes(), previous)
        reset = scout.prepare(self.papers, self.review_path, overwrite=True)
        self.assertEqual(reset["evidence"], [])
        self.assertEqual(reset["candidates"][0]["review_status"], "needs_review")

    def test_prepare_never_overwrites_input_or_its_links(self):
        before = self.papers.read_bytes()
        aliases = [self.papers, self.root / "hardlink.json", self.root / "symlink.json"]
        os.link(self.papers, aliases[1])
        aliases[2].symlink_to(self.papers)
        for alias in aliases:
            with self.subTest(path=alias), self.assertRaisesRegex(ValueError, "must differ from the input"):
                scout.prepare(self.papers, alias, overwrite=True)
            self.assertEqual(self.papers.read_bytes(), before)

    def test_prepare_preserves_source_named_like_the_old_temporary_file(self):
        source = self.review_path.with_name(self.review_path.name + ".tmp")
        self.save(source, self.bundle)
        before = source.read_bytes()
        review = scout.prepare(source, self.review_path)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(review["paper_bundle"], self.bundle)
        self.assertEqual(json.loads(self.review_path.read_text())["paper_bundle"], self.bundle)

    def test_prepare_preserves_existing_user_tmp_file_and_cleans_its_own_on_failure(self):
        user_tmp = self.review_path.with_name(self.review_path.name + ".tmp")
        user_tmp.write_text("User content unrelated to preparation", encoding="utf-8")
        before = user_tmp.read_bytes()
        scout.prepare(self.papers, self.review_path)
        previous_review = self.review_path.read_bytes()
        self.assertEqual(user_tmp.read_bytes(), before)
        with patch.object(scout.os, "replace", side_effect=OSError("Injected prepare publication failure")):
            with self.assertRaisesRegex(OSError, "Injected prepare publication failure"):
                scout.prepare(self.papers, self.review_path, overwrite=True)
        self.assertEqual(user_tmp.read_bytes(), before)
        self.assertEqual(self.review_path.read_bytes(), previous_review)
        self.assertEqual(list(self.root.glob(".review.json.*.tmp")), [])

    def test_prepare_cli_overwrite_is_explicit(self):
        self.prepared()
        args = ["prepare", "--papers", str(self.papers), "--out", str(self.review_path)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(scout.main(args), 2)
            self.assertEqual(scout.main(args + ["--overwrite"]), 0)

    def test_person_evidence_must_bind_the_candidate_to_the_paper(self):
        review = self.prepared()
        candidate = complete_candidate(review)
        review["evidence"][1]["entity_ids"] = [candidate["person"]["id"]]
        with self.assertRaisesRegex(ValueError, "authorship evidence.*person.id.*candidate.paper_id"):
            self.rendered(review)
        review["evidence"][1]["entity_ids"].append(candidate["paper_id"])
        review["evidence"][1]["kind"] = "funding_announcement"
        with self.assertRaisesRegex(ValueError, "authorship evidence"):
            self.rendered(review)
        review["evidence"][1]["kind"] = "author_homepage"
        _, audit = self.rendered(review)
        self.assertEqual(audit["results"][0]["status"], "scored")

    def test_nonfinancing_evidence_dates_respect_review_snapshot(self):
        for eid in ("paper", "person"):
            review = self.prepared()
            complete_candidate(review)
            evidence = next(e for e in review["evidence"] if e["id"] == eid)
            evidence["checked_at"] = "2027-01-02"
            with self.subTest(evidence=eid), self.assertRaisesRegex(ValueError, "checked_at is after review.as_of"):
                self.rendered(review)
            evidence["checked_at"] = "2026-09-19"
            evidence["source_date"] = "2026-09-20"
            with self.subTest(evidence=eid), self.assertRaisesRegex(ValueError, "source_date is after checked_at"):
                self.rendered(review)
            evidence["source_date"] = None
            _, audit = self.rendered(review)
            self.assertEqual(audit["results"][0]["status"], "scored")

    def test_rerender_removes_only_obsolete_tool_profiles_from_this_run(self):
        review = self.prepared()
        candidate = complete_candidate(review)
        first, _ = self.rendered(review)
        old_profile = Path(first["profiles"][0])
        manual = old_profile.parent / "person-ffffffffffffffff.md"
        manual.write_text("Manual file even though it resembles a generated profile\n" + scout.GENERATED_PROFILE_MARKER)
        other_run = old_profile.parent.parent / "another-run" / old_profile.name
        other_run.parent.mkdir()
        other_run.write_bytes(old_profile.read_bytes())
        symlink = old_profile.parent / "person-aaaaaaaaaaaaaaaa.md"
        symlink.symlink_to(other_run)
        candidate["triage"].update(relevant=False, reason="Reviewed as out of scope")
        second, audit = self.rendered(review)
        self.assertEqual(second["profiles"], [])
        self.assertEqual(audit["generated_profiles"], [])
        self.assertFalse(old_profile.exists())
        self.assertTrue(manual.exists())
        self.assertTrue(other_run.exists())
        self.assertTrue(symlink.is_symlink())

    def test_failed_render_commit_restores_previous_report_audit_and_profiles(self):
        review = self.prepared()
        candidate = complete_candidate(review)
        first, _ = self.rendered(review)
        paths = [Path(first["report"]), Path(first["evidence"])] + [Path(p) for p in first["profiles"]]
        previous = {path: path.read_bytes() for path in paths}
        candidate["triage"].update(relevant=False, reason="Reviewed as out of scope")
        self.save(self.review_path, review)
        replace = scout.os.replace
        def fail_audit_once(source, destination):
            if Path(destination) == Path(first["evidence"]) and Path(source).name.startswith("new-"):
                raise OSError("Injected audit publication failure")
            return replace(source, destination)
        with patch.object(scout.os, "replace", side_effect=fail_audit_once):
            with self.assertRaisesRegex(OSError, "Injected audit publication failure"):
                scout.render(self.review_path, self.root / "data")
        for path in paths:
            self.assertEqual(path.read_bytes(), previous[path])
        self.assertEqual(list((self.root / "data").glob(".scout-render-*")), [])

    def test_staging_cleanup_failure_does_not_turn_published_output_into_failure(self):
        review = self.prepared()
        complete_candidate(review)
        with patch.object(scout.shutil, "rmtree", side_effect=OSError("Injected cleanup failure")), contextlib.redirect_stderr(io.StringIO()) as warning:
            result, audit = self.rendered(review)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(audit["status"], "complete")
        self.assertTrue(Path(result["profiles"][0]).exists())
        self.assertIn("staging cleanup failed", warning.getvalue())

    def test_keyboard_interrupt_after_real_replace_restores_every_old_output(self):
        review = self.prepared()
        candidate = complete_candidate(review)
        first, _ = self.rendered(review)
        paths = [Path(first["report"]), Path(first["evidence"])] + [Path(p) for p in first["profiles"]]
        previous = {path: path.read_bytes() for path in paths}
        candidate["assessment"]["technical"]["score"] = 1
        self.save(self.review_path, review)
        replace = scout.os.replace
        for interrupted_path in paths:
            with self.subTest(replaced_output=interrupted_path.name):
                def interrupt_after_replace(source, destination):
                    replace(source, destination)
                    if Path(source).name.startswith("new-") and Path(destination) == interrupted_path:
                        raise KeyboardInterrupt("Injected after completed replacement")
                with patch.object(scout.os, "replace", side_effect=interrupt_after_replace):
                    with self.assertRaises(KeyboardInterrupt):
                        scout.render(self.review_path, self.root / "data")
                for path in paths:
                    self.assertEqual(path.read_bytes(), previous[path])
                self.assertEqual(list((self.root / "data").glob(".scout-render-*")), [])

    def test_interrupt_during_rollback_keeps_remaining_recovery_files(self):
        review = self.prepared()
        candidate = complete_candidate(review)
        first, _ = self.rendered(review)
        old_report = Path(first["report"]).read_bytes()
        old_profile = Path(first["profiles"][0]).read_bytes()
        candidate["assessment"]["technical"]["score"] = 1
        self.save(self.review_path, review)
        replace = scout.os.replace
        def interrupt_during_recovery(source, destination):
            replace(source, destination)
            if Path(destination) == Path(first["evidence"]):
                if Path(source).name.startswith("new-"):
                    raise OSError("Injected publication failure after replacement")
                raise KeyboardInterrupt("Injected interruption during rollback")
        with patch.object(scout.os, "replace", side_effect=interrupt_during_recovery):
            with self.assertRaises(KeyboardInterrupt):
                scout.render(self.review_path, self.root / "data")
        recovery_dirs = list((self.root / "data").glob(".scout-render-*"))
        self.assertEqual(len(recovery_dirs), 1)
        recovery_contents = [p.read_bytes() for p in recovery_dirs[0].glob("old-*")]
        self.assertIn(old_report, recovery_contents)
        self.assertIn(old_profile, recovery_contents)

    def test_omitted_candidates_remain_unreviewed(self):
        second = copy.deepcopy(self.bundle["papers"][0])
        second["id"] = "arxiv:synthetic-002"
        self.bundle["papers"].append(second)
        self.save(self.papers, self.bundle)
        review = self.prepared(limit=1)
        complete_candidate(review)
        result, audit = self.rendered(review)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(audit["counts"]["omitted_unreviewed"], 1)

    def test_partial_source_run_is_not_reported_complete(self):
        review = self.prepared()
        complete_candidate(review)
        review["paper_bundle"]["run"]["status"] = "partial"
        result, _ = self.rendered(review)
        self.assertEqual(result["status"], "partial_source")

    def test_invalid_profile_and_parameters_are_rejected(self):
        profile = scout.load_profile()
        profile["weights"]["technical"] = 0.99
        path = self.root / "bad.yml"
        path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sum to 1"):
            scout.load_profile(path)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            self.prepared(limit=0)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            scout.render(self.review_path, self.root, top=0)

    def test_main_returns_nonzero_for_missing_review(self):
        self.assertEqual(scout.main(["render", "--review", str(self.root / "missing.json")]), 2)


if __name__ == "__main__":
    unittest.main()
