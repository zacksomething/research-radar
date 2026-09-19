"""An explicitly fictional offline exercise of the collection and review pipeline."""

import contextlib
import io
import json
import sys
from pathlib import Path


DISCLAIMER = (
    "SYNTHETIC OFFLINE DEMO: all papers, people, institutions, evidence and scores "
    "are fictional test data. No websites, LLM APIs or financing databases were queried."
)
SYNTHETIC_IDS = {"synthetic:world-model-demo", "synthetic:robot-demo"}


def fill_synthetic_review(review):
    """Populate only the bundled, allow-listed fictional fixture, never real papers."""
    papers = {paper["id"]: paper for paper in review["paper_bundle"]["papers"]}
    if set(papers) != SYNTHETIC_IDS or any("SYNTHETIC DEMO" not in paper["title"] for paper in papers.values()):
        raise ValueError("Synthetic review helper only accepts the two bundled fictional papers")
    review["synthetic_demo"] = True
    review["disclaimer"] = DISCLAIMER
    review["paper_bundle"]["run"]["synthetic_demo"] = True
    review["paper_bundle"]["run"]["disclaimer"] = DISCLAIMER
    review["evidence"] = []
    for candidate in review["candidates"]:
        paper = papers[candidate["paper_id"]]
        suffix = "a" if paper["id"] == "synthetic:world-model-demo" else "b"
        person_id = "synthetic-person-" + suffix
        paper_evidence = "synthetic-full-text-" + suffix
        person_evidence = "synthetic-person-evidence-" + suffix
        review["evidence"].extend([
            {
                "id": paper_evidence,
                "url": paper["pdf_url"],
                "checked_at": "2026-01-01T00:00:00Z",
                "source_date": "2026-01-01",
                "kind": "paper_full_text",
                "entity_ids": [paper["id"]],
                "note": "SYNTHETIC fixture standing in for a full-text review. No real paper exists and no URL was fetched.",
            },
            {
                "id": person_evidence,
                "url": "https://example.invalid/synthetic/researcher-" + suffix,
                "checked_at": "2026-01-01T00:00:00Z",
                "source_date": "2026-01-01",
                "kind": "author_homepage",
                "entity_ids": [person_id],
                "note": "SYNTHETIC fixture for a fictional researcher and professional contact. No real identity or contact is asserted.",
            },
        ])
        candidate["review_status"] = "complete"
        candidate["triage"] = {
            "status": "reviewed", "relevant": True, "abstract_score": 3,
            "reason": "Synthetic topic match supplied to test the offline review workflow.",
        }
        candidate["person"] = {
            "id": person_id, "name": "Synthetic Researcher " + suffix.upper(),
            "status": "verified", "role": "Fictional demo researcher",
            "affiliation": "Synthetic Lab " + suffix.upper(),
            "evidence_ids": [person_evidence],
        }
        for dimension in ("technical", "team", "thesis_fit", "reachability"):
            item = candidate["assessment"][dimension]
            item["score"] = 4 if suffix == "a" and dimension in {"technical", "thesis_fit"} else 3
            item["reason"] = "Synthetic demonstration value; this is not a judgment about real research or people."
            item["evidence_ids"] = [person_evidence if dimension in {"team", "reachability"} else paper_evidence]
            if dimension == "technical":
                item["scope"] = "full_text"
        # Company, relationship, financing and visibility remain explicitly unknown.
        candidate["notes"] = [DISCLAIMER, "Unknown financing is not treated as unfunded or early-stage."]
    return review


def run_demo(data_dir):
    from . import collection, scout

    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    source_path = Path(__file__).resolve().parent / "resources" / "synthetic_papers.json"
    input_path = data_dir / "synthetic-input.json"
    input_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    review_path = data_dir / "synthetic-review.json"
    manifest_path = data_dir / "demo-manifest.json"
    print(DISCLAIMER)
    try:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = collection.main([
                "--source", "file", "--input", str(input_path),
                "--data-dir", str(data_dir), "--date", "2026-01-01", "--all",
            ])
        if status != 0:
            raise ValueError("Offline collection failed with exit code {}: {}".format(status, output.getvalue().strip()))
        collected = json.loads(output.getvalue())
        if collected.get("status") not in {"complete", "success"}:
            raise ValueError("Offline collection did not complete: " + str(collected.get("status")))
        papers_path = Path(collected["paths"]["papers"])
        review = scout.prepare(papers_path, review_path)
        fill_synthetic_review(review)
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rendered = scout.render(review_path, data_dir, top=2)
        if rendered["status"] != "complete" or rendered["scored"] != 2:
            raise ValueError("Synthetic review did not produce two complete fictional candidates")
        manifest = {
            "schema_version": 1,
            "synthetic_demo": True,
            "offline": True,
            "disclaimer": DISCLAIMER,
            "status": "complete",
            "input": str(input_path),
            "papers": str(papers_path),
            "review": str(review_path),
            "report": rendered["report"],
            "evidence": rendered["evidence"],
            "profiles": rendered["profiles"],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "complete", "synthetic_demo": True, "manifest": str(manifest_path), "report": rendered["report"]}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("demo: " + str(exc), file=sys.stderr)
        return 2
