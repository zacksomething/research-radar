---
name: paper-sweep
description: Discover and triage recent arXiv and Hugging Face papers, or import a conference paper list. Use for daily paper scans, research queues, and topic digests. Produces complete structured paper records and a readable report; use talent-scout when the user also wants researcher or team investigation.
---

# Paper sweep

Answer “what recent work is worth reading?” Use the Research Radar CLI for collection and the host model for interpretation. The keyword/regex prior is a cheap ordering signal, not a claim that a paper is strong or SOTA.

## Runtime

Run `research-radar doctor` first. If the command is unavailable in this checkout, use `.venv/bin/python -m research_radar`; elsewhere use the Python environment in which the user installed Research Radar. Do not hardcode a user's home directory or create an unrelated vault. The repository README contains installation instructions.

## Collect

```bash
research-radar sweep --source both --days 2 --data-dir data
research-radar sweep --source arxiv --days 7 --cluster World-Models --all --data-dir data
research-radar sweep --source file --input conference-papers.json --data-dir data
```

Use `--date YYYY-MM-DD` to anchor a historical window. The default is yesterday in UTC. arXiv submission dates and HF featured dates have different meanings; report which is used. `--source file` accepts a JSON paper list or an existing paper bundle; do not describe an imported list as a fresh online search.

Read the JSON summary printed by the CLI to find this run's `papers.json`, `run.json`, and report. Read the full machine records when assessing papers. All collected records, including papers that do not match keywords, remain in the bundle. `--cluster`, `--top` and `--all` control the reading view, not the completeness of that bundle. Check the source status and truncation flag before describing coverage.

## Triage and deliver

- Review the title and full abstract for relevance; correct the primary cluster and keep useful secondary labels in your analysis. Sample the unmatched pool before asserting that a topic has no new work.
- Distinguish the author's claims, the heuristic prior, and your assessment. For strong novelty or performance claims, read the relevant methods, experiments, limitations, and code before endorsing them.
- Write concise explanations of the problem, contribution, evidence, and remaining uncertainty. Link the original paper.
- Report new versus previously seen records, the date window, and any partial source failure. Never turn “fetch failed” into “no papers today.” Exit 0 means collection succeeded; exit 2 can mean partial/truncated collection or invalid arguments; read the JSON/error before deciding whether usable records exist. Exit 1 means failure.
- For team investigation, pass the same `papers.json` to talent-scout; do not refetch or parse a shortened Markdown preview.

## Configuration and operation

Defaults are packaged with Research Radar. Pass `--config /path/to/clusters.yml` for a custom topic profile; the YAML beside this skill is an editable example. The same data directory maintains cross-run state. Retry within the CLI's bounded retry policy; broaden the window only when it addresses a real coverage gap, not to fill a quota. Scheduling is optional host orchestration and is not installed by this skill.
