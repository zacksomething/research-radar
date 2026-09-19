import copy
import json
from pathlib import Path
import tempfile
import unittest

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
         "source_date": "2026-09-18", "kind": "author_homepage", "entity_ids": [person_id],
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
        review = scout.prepare(self.papers, self.review_path, limit)
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
        for date in (None, "2020-01-01", "2027-01-01"):
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
        _, audit = self.rendered(review)
        self.assertEqual(audit["results"][0]["components"]["effective_stage"], "unknown")

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
