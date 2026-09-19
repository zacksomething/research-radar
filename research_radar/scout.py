"""Prepare host-researched reviews and deterministically render evidence-backed radar.

This module performs no web searches or language-model calls. Its checks establish
data consistency and provenance coverage, not the truth of a cited web page.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from urllib.parse import urlparse

import yaml


REVIEW_SCHEMA = "research-radar-scout/v1"
STATUSES = {"verified", "ambiguous", "not_found", "inaccessible", "unknown", "stale"}
ASSESSMENTS = ("technical", "team", "thesis_fit", "reachability")
EVIDENCE_KINDS = {
    "paper_full_text", "paper_abstract", "code", "independent_evaluation",
    "author_homepage", "organization", "company", "funding_announcement",
    "database", "news", "other",
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _number(value, label, lo=0, hi=5):
    _require(isinstance(value, (int, float)) and not isinstance(value, bool)
             and math.isfinite(value) and lo <= value <= hi,
             "{} must be a finite number in [{}, {}]".format(label, lo, hi))
    return float(value)


def _text(value, label):
    _require(isinstance(value, str) and bool(value.strip()), label + " must be nonempty text")
    return value.strip()


def _stable_id(prefix, value):
    return prefix + "-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def new_candidate(paper):
    """Public template builder; all facts and assessments start unresolved."""
    assessment = {
        name: {"score": None, "reason": "", "evidence_ids": []}
        for name in ASSESSMENTS
    }
    assessment["technical"]["scope"] = "unknown"
    return {
        "id": _stable_id("candidate", paper["id"]),
        "paper_id": paper["id"],
        "review_status": "needs_review",
        "triage": {"status": "unknown", "relevant": None, "abstract_score": None, "reason": ""},
        "person": {"id": None, "name": None, "status": "unknown", "role": None,
                   "affiliation": None, "evidence_ids": []},
        "company": {"id": None, "name": None, "status": "unknown", "evidence_ids": []},
        "relationship": {"status": "unknown", "type": None, "person_id": None,
                         "company_id": None, "evidence_ids": []},
        "financing": {"status": "unknown", "stage": "unknown", "confidence": "low",
                      "company_id": None, "evidence_ids": []},
        "assessment": assessment,
        "visibility": {"status": "unknown", "value": "unknown", "evidence_ids": []},
        "notes": [],
    }


def prepare(papers_path, output_path, limit=None):
    """Keep the complete source bundle; an explicit limit marks omitted papers."""
    if limit is not None:
        _require(isinstance(limit, int) and not isinstance(limit, bool) and limit > 0,
                 "limit must be a positive integer")
    bundle = _read_json(papers_path)
    _require(isinstance(bundle, dict) and bundle.get("schema_version") == 1,
             "papers schema_version must be 1")
    _require(isinstance(bundle.get("run"), dict), "papers.run must be an object")
    _text(bundle["run"].get("id"), "papers.run.id")
    papers = bundle.get("papers")
    _require(isinstance(papers, list), "papers must be an array")
    seen = set()
    for paper in papers:
        _require(isinstance(paper, dict), "each paper must be an object")
        paper_id = _text(paper.get("id"), "paper.id")
        _require(paper_id not in seen, "duplicate paper id: " + paper_id)
        seen.add(paper_id)
        _text(paper.get("title"), "paper.title")
    selected = papers if limit is None else papers[:limit]
    review = {
        "schema_version": 1,
        "review_schema": REVIEW_SCHEMA,
        "as_of": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "paper_bundle": bundle,
        "selection": {"limit": limit, "total": len(papers), "selected": len(selected),
                      "omitted_paper_ids": [p["id"] for p in papers[len(selected):]],
                      "method": "input_order; explicit limit inherits upstream ordering (which may be prior-ranked); omitted papers remain unreviewed"},
        "evidence": [],
        "candidates": [new_candidate(paper) for paper in selected],
    }
    _write_json(output_path, review)
    return review


def load_profile(path=None):
    profile_path = Path(path) if path else Path(__file__).parent / "resources" / "scout_rubric.yml"
    with profile_path.open(encoding="utf-8") as stream:
        profile = yaml.safe_load(stream)
    _require(isinstance(profile, dict) and profile.get("schema_version") == 1,
             "profile schema_version must be 1")
    _text(profile.get("version"), "profile.version")
    weights = profile.get("weights", {})
    _require(isinstance(weights, dict), "profile.weights must be an object")
    _require(set(weights) == set(ASSESSMENTS), "profile.weights must define exactly " + ", ".join(ASSESSMENTS))
    for name, value in weights.items():
        _number(value, "weight " + name, 0, 1)
    _require(abs(sum(weights.values()) - 1) < 1e-9, "profile weights must sum to 1")
    stages = profile.get("stage_multipliers", {})
    _require(isinstance(stages, dict) and "unknown" in stages, "stage_multipliers needs unknown")
    for stage, value in stages.items():
        _text(stage, "stage key")
        _number(value, "stage multiplier " + stage, 0, 1)
    drops = profile.get("hard_drop_stages")
    _require(isinstance(drops, list) and all(stage in stages and stage != "unknown" for stage in drops),
             "hard_drop_stages must reference known, non-unknown stages")
    bonuses = profile.get("visibility_bonus", {})
    _require(isinstance(bonuses, dict), "visibility_bonus must be an object")
    _require(set(bonuses) == {"unknown", "low", "medium", "high"}, "visibility_bonus needs unknown/low/medium/high")
    for key, value in bonuses.items():
        _number(value, "visibility bonus " + key, 0, 1)
    _require(bonuses["unknown"] == 0, "unknown visibility must have zero bonus")
    age = profile.setdefault("financing_max_age_days", 365)
    _require(isinstance(age, int) and not isinstance(age, bool) and 1 <= age <= 3650,
             "financing_max_age_days must be an integer in [1, 3650]")
    return profile


def _date(value, label, nullable=False):
    if nullable and value is None:
        return
    _text(value, label)
    try:
        dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(label + " must be an ISO date or timestamp")


def _evidence_index(review):
    items = review.get("evidence")
    _require(isinstance(items, list), "evidence must be an array")
    index = {}
    for item in items:
        _require(isinstance(item, dict), "evidence item must be an object")
        eid = _text(item.get("id"), "evidence.id")
        _require(eid not in index, "duplicate evidence id: " + eid)
        url = urlparse(_text(item.get("url"), "evidence.url"))
        _require(url.scheme in {"http", "https"} and bool(url.netloc) and not url.username and not url.password,
                 "evidence.url must be a public HTTP(S) URL without credentials")
        _date(item.get("checked_at"), "evidence.checked_at")
        _require("source_date" in item, "evidence.source_date is required (null allowed)")
        _date(item["source_date"], "evidence.source_date", nullable=True)
        _require(item.get("kind") in EVIDENCE_KINDS, "unsupported evidence.kind: " + str(item.get("kind")))
        _require(isinstance(item.get("entity_ids"), list)
                 and all(isinstance(v, str) and v.strip() for v in item["entity_ids"]),
                 "evidence.entity_ids must be an array of identifiers")
        _text(item.get("note"), "evidence.note")
        index[eid] = item
    return index


def _refs(obj, label, evidence, required=False, entity=None, kinds=None):
    ids = obj.get("evidence_ids")
    _require(isinstance(ids, list) and all(isinstance(v, str) for v in ids), label + ".evidence_ids must be an array")
    _require(len(ids) == len(set(ids)), label + " has duplicate evidence ids")
    _require(all(eid in evidence for eid in ids), label + " references missing evidence")
    if required:
        _require(bool(ids), label + " requires evidence")
    selected = [evidence[eid] for eid in ids]
    if required and entity:
        _require(any(entity in item["entity_ids"] for item in selected),
                 label + " requires evidence bound to entity " + entity)
    if required and kinds:
        _require(any(item["kind"] in kinds and (not entity or entity in item["entity_ids"]) for item in selected),
                 label + " requires an appropriate source kind for its entity")
    return selected


def _fact(candidate, field, evidence):
    value = candidate.get(field)
    _require(isinstance(value, dict), field + " must be an object")
    _require(value.get("status") in STATUSES, field + ".status is invalid")
    _refs(value, field, evidence, required=value["status"] == "verified")
    if value["status"] in {"not_found", "inaccessible", "ambiguous", "stale"}:
        _text(value.get("reason"), field + ".reason")
        _refs(value, field, evidence, required=True)
    return value


def _financing_freshness(financing, company, evidence, profile, as_of):
    """A dated funding snapshot is usable only within the declared review window."""
    max_age = profile["financing_max_age_days"]
    dated = []
    for eid in financing["evidence_ids"]:
        item = evidence[eid]
        if (item["kind"] not in {"funding_announcement", "company", "database", "news"}
                or company.get("id") not in item["entity_ids"]):
            continue
        if item["source_date"] is None:
            continue
        source_date = dt.datetime.fromisoformat(item["source_date"].replace("Z", "+00:00")).date()
        checked_date = dt.datetime.fromisoformat(item["checked_at"].replace("Z", "+00:00")).date()
        if (source_date <= checked_date <= as_of
                and 0 <= (as_of - source_date).days <= max_age
                and 0 <= (as_of - checked_date).days <= max_age):
            dated.append(eid)
    return {"usable": bool(dated), "as_of": as_of.isoformat(), "max_age_days": max_age,
            "eligible_evidence_ids": dated,
            "reason": ("Dated financing evidence is within the review window" if dated else
                       "Financing evidence has no usable source date, is stale, or falls after the review snapshot")}


def assess_candidate(candidate, papers, evidence, profile, as_of=None):
    """Validate one candidate. Missing work stays pending; invalid claims fail closed."""
    _require(isinstance(candidate, dict), "candidate must be an object")
    cid = _text(candidate.get("id"), "candidate.id")
    paper_id = _text(candidate.get("paper_id"), "candidate.paper_id")
    _require(paper_id in papers, "candidate references an unknown paper: " + paper_id)
    review_status = candidate.get("review_status")
    _require(review_status in {"needs_review", "complete"}, "invalid review_status")
    triage = candidate.get("triage")
    _require(isinstance(triage, dict) and triage.get("status") in {"unknown", "reviewed"}, "invalid triage.status")
    if triage["status"] == "reviewed":
        _require(isinstance(triage.get("relevant"), bool), "reviewed triage requires boolean relevant")
        _number(triage.get("abstract_score"), "triage.abstract_score")
        _text(triage.get("reason"), "triage.reason")
    person = _fact(candidate, "person", evidence)
    company = _fact(candidate, "company", evidence)
    relation = _fact(candidate, "relationship", evidence)
    financing = _fact(candidate, "financing", evidence)
    visibility = _fact(candidate, "visibility", evidence)
    for field, value in (("person", person), ("company", company)):
        if value["status"] == "verified":
            entity_id = _text(value.get("id"), field + ".id")
            _text(value.get("name"), field + ".name")
            _refs(value, field, evidence, required=True, entity=entity_id)
    if person["status"] == "verified":
        _text(person.get("role"), "person.role")
    if relation["status"] == "verified":
        _require(person["status"] == "verified" and company["status"] == "verified",
                 "verified relationship requires verified person and company")
        _require(relation.get("person_id") == person["id"] and relation.get("company_id") == company["id"],
                 "relationship person/company identifiers do not match")
        _require(relation.get("type") in {"founder", "cofounder", "employee", "advisor", "research_collaborator"},
                 "relationship.type must explicitly describe the evidenced relationship")
        refs = _refs(relation, "relationship", evidence, required=True)
        _require(any(person["id"] in e["entity_ids"] and company["id"] in e["entity_ids"] for e in refs),
                 "relationship evidence must bind both person and company")
    stage = financing.get("stage")
    _require(stage in profile["stage_multipliers"], "invalid financing.stage: " + str(stage))
    confidence = financing.get("confidence")
    _require(confidence in {"low", "medium", "high"}, "financing.confidence must be low/medium/high")
    if financing["status"] in {"verified", "stale"} and stage != "unknown":
        _require(company["status"] == "verified" and financing.get("company_id") == company["id"],
                 "financing company identifier does not match verified company")
        _refs(financing, "financing", evidence, required=True, entity=company["id"],
              kinds={"funding_announcement", "company", "database", "news"})
    else:
        _require(financing["status"] != "verified", "verified financing cannot have unknown stage")
        _require(stage == "unknown", "unverified financing must use stage unknown")
    _require(visibility.get("value") in {"unknown", "low", "medium", "high"}, "invalid visibility.value")
    if visibility["status"] == "verified":
        _require(visibility["value"] != "unknown", "verified visibility requires a value")
        _text(visibility.get("reason"), "visibility.reason")
        _refs(visibility, "visibility", evidence, required=True,
              entity=company.get("id") if company["status"] == "verified" else person.get("id"))
    else:
        _require(visibility["value"] == "unknown", "unverified visibility must use value unknown")
    assessment = candidate.get("assessment")
    _require(isinstance(assessment, dict), "assessment must be an object")
    missing = []
    scores = {}
    for name in ASSESSMENTS:
        item = assessment.get(name)
        _require(isinstance(item, dict), "assessment." + name + " must be an object")
        refs = _refs(item, "assessment." + name, evidence)
        if item.get("score") is None:
            missing.append("assessment." + name)
            continue
        scores[name] = _number(item["score"], "assessment." + name + ".score")
        _text(item.get("reason"), "assessment." + name + ".reason")
        _require(bool(refs), "assessment." + name + " requires evidence")
        if name == "technical":
            _require(item.get("scope") == "full_text", "technical score requires full_text scope; abstract triage is separate")
            _require(any(e["kind"] in {"paper_full_text", "code", "independent_evaluation"}
                         and paper_id in e["entity_ids"] for e in refs),
                     "technical score requires substantive evidence bound to the paper")
        elif name in {"team", "reachability"}:
            _require(person["status"] == "verified" and any(person["id"] in e["entity_ids"] for e in refs),
                     name + " score requires evidence for the verified person")
        elif name == "thesis_fit":
            _require(any(paper_id in e["entity_ids"] or (company.get("id") and company["id"] in e["entity_ids"]) for e in refs),
                     "thesis_fit score requires paper or company evidence")
    if triage["status"] != "reviewed":
        missing.append("triage")
    if person["status"] != "verified":
        missing.append("person")
    if review_status != "complete":
        missing.append("host_review_completion")
    result = {"candidate_id": cid, "paper_id": paper_id, "status": "needs_review", "score": None,
              "missing": missing, "reasons": [], "components": {}, "candidate": candidate}
    freshness = _financing_freshness(financing, company, evidence, profile,
                                    as_of or dt.datetime.now(dt.timezone.utc).date())
    result["financing_freshness"] = freshness
    if triage["status"] == "reviewed" and not triage["relevant"]:
        result.update(status="out_of_scope", reasons=[triage["reason"]])
        return result
    # A funding event can be real without being applicable to this particular person.
    attributable_relationship = (relation["status"] == "verified"
                                 and relation.get("type") in {"founder", "cofounder"})
    stage_usable = (financing["status"] == "verified" and confidence in {"medium", "high"}
                    and attributable_relationship and freshness["usable"])
    result["effective_stage"] = stage if stage_usable else "unknown"
    if stage_usable and stage in profile["hard_drop_stages"]:
        result.update(status="excluded_stage", reasons=["Verified company relationship and evidenced " + stage + " stage"])
        return result
    if missing:
        result["reasons"] = ["Research is incomplete; no score was manufactured"]
        return result
    effective_stage = stage if stage_usable else "unknown"
    multiplier = profile["stage_multipliers"][effective_stage]
    # An unknown financing stage never receives a low-visibility / underwater bonus.
    bonus = (profile["visibility_bonus"][visibility["value"]]
             if stage_usable and visibility["status"] == "verified" else 0.0)
    base = sum(scores[name] / 5 * profile["weights"][name] for name in ASSESSMENTS)
    score = round(min(10.0, max(0.0, 10 * base * multiplier * (1 + bonus))), 2)
    result.update(status="scored", score=score, components={
        "scores_0_to_5": scores, "weights": profile["weights"], "base_0_to_1": round(base, 8),
        "effective_stage": effective_stage, "stage_multiplier": multiplier,
        "visibility_bonus": bonus, "formula": "round(min(10, 10 * sum(weight_i * score_i / 5) * stage_multiplier * (1 + visibility_bonus)), 2)",
    })
    if not stage_usable:
        result["reasons"].append("Stage unknown or not attributable with sufficient confidence; uncertainty multiplier and no visibility bonus applied")
        if relation["status"] == "verified" and not attributable_relationship:
            result["reasons"].append("An employer, advisory or collaboration relationship does not transfer company financing to a founder candidate")
        if financing["status"] in {"verified", "stale"} and not freshness["usable"]:
            result["reasons"].append(freshness["reason"])
        if financing["status"] == "stale":
            result["reasons"].append("Financing was explicitly marked stale")
    return result


def _md(value):
    return str(value if value is not None else "—").replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("<", "&lt;").replace(">", "&gt;")


def _link(title, url):
    if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"}:
        return _md(title)
    safe = url.replace(" ", "%20").replace("(", "%28").replace(")", "%29").replace("\n", "").replace("\r", "")
    return "[{}]({})".format(_md(title).replace("[", "\\[").replace("]", "\\]"), safe)


def render(review_path, data_dir, profile_path=None, top=10):
    _require(isinstance(top, int) and not isinstance(top, bool) and top > 0, "top must be a positive integer")
    review = _read_json(review_path)
    _require(isinstance(review, dict) and review.get("schema_version") == 1 and review.get("review_schema") == REVIEW_SCHEMA,
             "unsupported review schema")
    profile = load_profile(profile_path)
    bundle = review.get("paper_bundle")
    _require(isinstance(bundle, dict) and bundle.get("schema_version") == 1, "review needs original paper_bundle")
    _require(isinstance(bundle.get("run"), dict), "paper_bundle.run must be an object")
    run_id = _text(bundle["run"].get("id"), "run.id")
    as_of_value = review.get("as_of") or bundle["run"].get("created_at") or bundle["run"].get("window", {}).get("end")
    _date(as_of_value, "review.as_of (or run.created_at/window.end)")
    as_of = dt.datetime.fromisoformat(as_of_value.replace("Z", "+00:00")).date()
    _require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id) is not None, "run.id contains unsafe filename characters")
    _require(isinstance(bundle.get("papers"), list), "paper_bundle.papers must be an array")
    papers = {}
    for paper in bundle["papers"]:
        _require(isinstance(paper, dict), "each paper must be an object")
        pid = _text(paper.get("id"), "paper.id")
        _require(pid not in papers, "duplicate paper id: " + pid)
        papers[pid] = paper
    evidence = _evidence_index(review)
    candidates = review.get("candidates")
    _require(isinstance(candidates, list), "candidates must be an array")
    results = [assess_candidate(c, papers, evidence, profile, as_of=as_of) for c in candidates]
    _require(len({r["candidate_id"] for r in results}) == len(results), "duplicate candidate id")
    _require(len({r["paper_id"] for r in results}) == len(results), "duplicate candidate paper_id")
    omitted = sorted(set(papers) - {r["paper_id"] for r in results})
    selection = review.get("selection")
    _require(isinstance(selection, dict) and isinstance(selection.get("omitted_paper_ids"), list)
             and all(isinstance(value, str) for value in selection["omitted_paper_ids"])
             and sorted(selection["omitted_paper_ids"]) == omitted,
             "selection.omitted_paper_ids must account for every paper without a candidate")
    _require(selection.get("total") == len(papers) and selection.get("selected") == len(results), "selection counts do not match bundle")
    ranked = sorted((r for r in results if r["status"] == "scored"), key=lambda r: (-r["score"], r["candidate_id"]))
    # Multiple papers about the same company/person yield one outreach suggestion.
    unique, seen_entities = [], set()
    for result in ranked:
        candidate = result["candidate"]
        founder_link = (candidate["relationship"]["status"] == "verified"
                        and candidate["relationship"].get("type") in {"founder", "cofounder"})
        entity = ("company", candidate["company"]["id"]) if founder_link else ("person", candidate["person"]["id"])
        if entity in seen_entities:
            result["duplicate_entity"] = True
            continue
        seen_entities.add(entity)
        unique.append(result)
    selected = unique[:top]
    pending = [r for r in results if r["status"] == "needs_review"]
    displayed = selected + pending[:max(0, top - len(selected))]
    overall_status = "needs_review" if pending or omitted else "complete"
    if bundle["run"].get("status") not in {"complete", "success", "ok"}:
        overall_status = "partial_source" if overall_status == "complete" else "needs_review_partial_source"
    target = Path(data_dir) / "Research"
    target.mkdir(parents=True, exist_ok=True)
    stem = "团队雷达_" + run_id
    report_path = target / (stem + ".md")
    audit_path = target / (stem + ".evidence.json")
    rows = ["# 团队雷达 {} · {} · 已评分 {} · 待审 {}\n".format(run_id, overall_status, len(unique), len(pending) + len(omitted)),
            "| # | 论文 | 研究者 | 机构 | 公司/关系 | 阶段(置信) | 推送分 | 状态 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for index, result in enumerate(displayed, 1):
        candidate, paper = result["candidate"], papers[result["paper_id"]]
        person, company, relation, financing = [candidate[k] for k in ("person", "company", "relationship", "financing")]
        stage_label = (financing["stage"] + " (" + financing["confidence"] + ")") if financing["status"] == "verified" else "unknown (" + financing["status"] + ")"
        if financing["stage"] != "unknown" and result.get("effective_stage") == "unknown":
            stage_label = "unknown; 已记录 " + financing["stage"] + " (" + financing["status"] + "; 未用于评分)"
        company_label = ((company.get("name") or "未知") + " (" + company["status"] + ") / "
                         + (relation.get("type") or relation["status"]))
        rows.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            index, _link(paper.get("title", result["paper_id"]), paper.get("url")),
            _md(person.get("name")), _md(person.get("affiliation")), _md(company_label),
            _md(stage_label), "—" if result["score"] is None else "{:.2f}".format(result["score"]), result["status"]))
    if not displayed:
        rows.append("| — | 无可展示候选 | — | — | — | — | — | {} |".format(overall_status))
    report_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    audit = {"schema_version": 1, "review_schema": REVIEW_SCHEMA, "run": bundle["run"],
             "as_of": as_of.isoformat(),
             "status": overall_status, "profile": profile, "selection": selection,
             "counts": {"scored_candidates": len(ranked), "unique_entities": len(unique),
                        "pending_candidates": len(pending), "omitted_unreviewed": len(omitted),
                        "displayed_scored": len(selected)},
             "evidence": review["evidence"], "results": results,
             "limitations": "Validation checks references and entity consistency, not whether cited pages substantiate human/model claims."}
    _write_json(audit_path, audit)
    profile_paths = []
    generated = Path(data_dir) / "Researchers" / "generated" / run_id
    for result in selected[:3]:
        person = result["candidate"]["person"]
        path = generated / (_stable_id("person", person["id"]) + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# {}\n\n自动生成；人工笔记请写在 Researchers/ 的其他路径。\n\n- Stable ID: {}\n- 角色: {}\n- 机构: {}\n- 推送分: {:.2f}\n- 证据与分项: [审核记录](../../../Research/{})\n- 来源论文: {}\n".format(
            _md(person["name"]), _md(person["id"]), _md(person["role"]), _md(person.get("affiliation")), result["score"],
            audit_path.name, _link(papers[result["paper_id"]].get("title"), papers[result["paper_id"]].get("url"))), encoding="utf-8")
        profile_paths.append(str(path))
    return {"status": overall_status, "report": str(report_path), "evidence": str(audit_path), "profiles": profile_paths,
            "scored": len(unique), "needs_review": len(pending) + len(omitted)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare", help="Create an unfilled review package for host research")
    prepare_parser.add_argument("--papers", required=True)
    prepare_parser.add_argument("--out", required=True)
    prepare_parser.add_argument("--limit", type=int)
    render_parser = commands.add_parser("render", help="Validate and render a host-researched review package")
    render_parser.add_argument("--review", required=True)
    render_parser.add_argument("--data-dir", default="data")
    render_parser.add_argument("--profile")
    render_parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            review = prepare(args.papers, args.out, args.limit)
            print(json.dumps({"review": str(args.out), "selected": len(review["candidates"]),
                              "unreviewed_omitted": len(review["selection"]["omitted_paper_ids"])}, ensure_ascii=False))
        else:
            print(json.dumps(render(args.review, args.data_dir, args.profile, args.top), ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        print("scout: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
