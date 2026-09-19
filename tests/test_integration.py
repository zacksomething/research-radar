"""Offline integration checks for the installed CLI contract."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_radar.__main__ import main
from research_radar.demo import fill_synthetic_review


class CommandLineTests(unittest.TestCase):
    def test_top_level_help(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main([]), 0)
        for command in ("sweep", "scout", "doctor", "demo"):
            self.assertIn(command, output.getvalue())

    def test_module_entry_point(self):
        result = subprocess.run(
            [sys.executable, "-m", "research_radar", "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0.1.0")

    def test_doctor_checks_bundled_configs_offline(self):
        output = io.StringIO()
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("doctor must not use the network")):
            with contextlib.redirect_stdout(output):
                status = main(["doctor", "--json"])
        report = json.loads(output.getvalue())
        self.assertEqual(status, 0, report)
        self.assertEqual(report["status"], "ok")
        names = {check["name"] for check in report["checks"]}
        self.assertTrue({"python", "pyyaml", "clusters", "rubric", "collection", "scout", "demo"} <= names)

    def test_doctor_rejects_invalid_config(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "broken.yml"
            broken.write_text("clusters: []\nsources: {}\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["doctor", "--config", str(broken), "--json"])
            report = json.loads(output.getvalue())
            self.assertEqual(status, 1)
            self.assertTrue(any(item["name"] == "clusters" and item["status"] == "error" for item in report["checks"]))

    def test_doctor_reports_missing_rubric(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["doctor", "--rubric", str(Path(directory) / "missing.yml"), "--json"])
            self.assertEqual(status, 1)
            report = json.loads(output.getvalue())
            self.assertTrue(any(item["name"] == "rubric" and item["status"] == "error" for item in report["checks"]))

    def test_doctor_rejects_semantically_invalid_rubric(self):
        with tempfile.TemporaryDirectory() as directory:
            rubric = Path(directory) / "invalid-rubric.yml"
            rubric.write_text("not_a_rubric: true\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["doctor", "--rubric", str(rubric), "--json"])
            self.assertEqual(status, 1)
            report = json.loads(output.getvalue())
            self.assertTrue(any(item["name"] == "rubric" and item["status"] == "error" for item in report["checks"]))

    def test_demo_is_offline_complete_and_safe_to_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            for attempt in range(2):
                output = io.StringIO()
                with mock.patch("urllib.request.urlopen", side_effect=AssertionError("offline demo attempted a network request")):
                    with contextlib.redirect_stdout(output):
                        status = main(["demo", "--data-dir", directory])
                self.assertEqual(status, 0, output.getvalue())
                self.assertIn("SYNTHETIC OFFLINE DEMO", output.getvalue())
                manifest = json.loads((Path(directory) / "demo-manifest.json").read_text(encoding="utf-8"))
                self.assertTrue(manifest["offline"])
                self.assertTrue(manifest["synthetic_demo"])
                self.assertEqual(manifest["status"], "complete")
                papers = json.loads(Path(manifest["papers"]).read_text(encoding="utf-8"))
                self.assertEqual(len(papers["papers"]), 2)
                self.assertEqual(len({paper["id"] for paper in papers["papers"]}), 2)
                if attempt == 1:
                    self.assertEqual({paper["change"] for paper in papers["papers"]}, {"seen"})
                evidence = json.loads(Path(manifest["evidence"]).read_text(encoding="utf-8"))
                self.assertEqual(evidence["counts"]["scored_candidates"], 2)
                self.assertEqual(evidence["counts"]["pending_candidates"], 0)
                self.assertTrue(all(item["components"]["effective_stage"] == "unknown" for item in evidence["results"]))
                report = Path(manifest["report"]).read_text(encoding="utf-8")
                self.assertEqual(report.count("SYNTHETIC DEMO:"), 2)
                self.assertNotIn("needs_review", report)
                self.assertEqual(len(manifest["profiles"]), 2)
                for profile_path in manifest["profiles"]:
                    self.assertIn("Synthetic Researcher", Path(profile_path).read_text(encoding="utf-8"))

    def test_synthetic_helper_rejects_real_input(self):
        with self.assertRaises(ValueError):
            fill_synthetic_review({"paper_bundle": {"papers": [{"id": "arxiv:2601.00001", "title": "A real-looking paper"}]}})

    def test_public_synthetic_example_matches_packaged_fixture(self):
        root = Path(__file__).resolve().parents[1]
        example = root / "examples" / "synthetic_papers.json"
        bundled = root / "research_radar" / "resources" / "synthetic_papers.json"
        self.assertEqual(json.loads(example.read_text(encoding="utf-8")), json.loads(bundled.read_text(encoding="utf-8")))

    def test_public_review_example_renders_offline(self):
        review = Path(__file__).resolve().parents[1] / "examples" / "synthetic_review.json"
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with mock.patch("urllib.request.urlopen", side_effect=AssertionError("render must not browse")):
                with contextlib.redirect_stdout(output):
                    status = main(["scout", "render", "--review", str(review), "--data-dir", directory, "--top", "2"])
            self.assertEqual(status, 0, output.getvalue())
            self.assertEqual(json.loads(output.getvalue())["scored"], 2)


if __name__ == "__main__":
    unittest.main()
