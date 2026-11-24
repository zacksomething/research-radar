# Evidence protocol

Research Radar keeps collection, research judgments, and report rendering separate. A complete packet means the work and uncertainty are recorded, not that every field is known. Run `research-radar scout validate --review <path>` during editing to catch contract violations early without writing reports.

## Evidence entries

Use the `scout prepare` output as the schema template. Each citation needs a unique `id`, a public HTTP(S) `url` of the page actually checked (whitespace, credentials, single-label intranet hosts, placeholder domains like `example.org`/`*.invalid`/`*.test`, local/private IPs, and non-canonical IP notations are rejected outside the synthetic demo), `checked_at`, `source_date` (`null` allowed when unknown), `kind`, `entity_ids`, and a concise `note` describing the supported fact. Quote only a short relevant passage when useful. A homepage URL with no observation is not a verified claim.

An illustrative evidence entry (replace every example fact before use):

```json
{
  "id": "e-paper-1",
  "url": "https://arxiv.org/abs/REPLACE-WITH-CHECKED-PAGE",
  "checked_at": "2026-09-19",
  "source_date": null,
  "kind": "paper_full_text",
  "entity_ids": ["synthetic:paper-1", "person:synthetic-1"],
  "note": "SYNTHETIC example: record the exact section examined and the fact it supports."
}
```

Allowed `kind` values: `paper_full_text`, `paper_abstract`, `code`, `independent_evaluation`, `author_homepage`, `organization`, `company`, `funding_announcement`, `database`, `news`, `other`.

Dates: `checked_at` and non-null `source_date` accept ISO dates (`YYYY-MM-DD`) or ISO timestamps (timezone-aware timestamps are converted to UTC before date comparison). Every evidence item must satisfy `source_date <= checked_at <= as_of` (when `source_date` is non-null). `prepare` sets `prepared_at` and `as_of` to the current UTC date. When continuing research on a later day, run `scout refresh --review <path>` to advance `as_of` (preserving prior cutoffs in `as_of_history`) rather than backdating `checked_at`.

## Field and validation contract

The CLI enforces these exact rules per candidate (errors include `[candidate <id> / <paper_id>]`):

| Section | Required fields when active | Required `entity_ids` binding | Allowed `evidence.kind` |
|---|---|---|---|
| `triage` (`status: "reviewed"`) | `relevant` (bool), `abstract_score` (0–5), `reason` | — | — |
| `person` (`status: "verified"`) | `id`, `name` (matches byline ignoring case/diacritics/punctuation/order, or put byline spelling in `listed_name`; pinned to `author_index` if set), `role`, `evidence_ids` | At least one entry binds **both** `person.id` and `candidate.paper_id` | `paper_full_text`, `paper_abstract`, `author_homepage`, `organization` |
| `company` (`status: "verified"`) | `id`, `name`, `evidence_ids` | `company.id` | Any |
| `relationship` (`status: "verified"`) | Verified `person` & `company`, matching `person_id` & `company_id`, `type` in `{founder, cofounder, employee, advisor, research_collaborator}`, `evidence_ids` | At least one entry binds **both** `person.id` and `company.id` | Any |
| `financing` (`status: "verified"` or `"stale"` with `stage != "unknown"`) | Verified `company`, matching `company_id`, `stage` in rubric, `confidence` in `{low, medium, high}`, `evidence_ids` (plus `reason` when `"stale"`) | `company.id` | `funding_announcement`, `company`, `database`, `news` |
| `visibility` (`status: "verified"`) | Verified `company` or `person`, `value` in `{low, medium, high}`, `reason`, `evidence_ids` | `company.id` if company verified, else `person.id` | Any |
| `assessment.technical` (non-null `score`) | `score` (0–5), `reason`, `scope: "full_text"`, `evidence_ids` | `candidate.paper_id` | `paper_full_text`, `code`, `independent_evaluation` |
| `assessment.team` (non-null `score`) | Verified `person`, `score` (0–5), `reason`, `evidence_ids` | `person.id` | `author_homepage`, `organization`, `company`, `database`, `news` |
| `assessment.reachability` (non-null `score`) | Verified `person`, `score` (0–5), `reason`, `evidence_ids` | `person.id` | `author_homepage`, `organization`, `company` |
| `assessment.thesis_fit` (non-null `score`) | `score` (0–5), `reason`, `evidence_ids` | `candidate.paper_id` or verified `company.id` | Any |
| Any fact with `status` in `{not_found, inaccessible, ambiguous, stale}` | Non-empty `reason` **and** non-empty `evidence_ids` recording the attempted source(s) (keep `financing.stage: "unknown"` unless `stale`; keep `visibility.value: "unknown"`) | Optional (e.g. `[]` for failed lookup) | Any |
| `review_status: "complete"` | `open_questions` must be a list of non-empty strings (`[]` only when nothing material remains uncertain) | — | — |

Default financing stages: `unknown`, `bootstrapped_confirmed`, `pre_seed`, `seed`, `angel`, `pre_a`, `pre_a_plus`, `a`, `a_plus`, `b`, `c_plus`, `public`, `acquired`. See `examples/synthetic_review.json` for a complete fixture; its values are test data, not reusable evidence.

## Candidate lifecycle states

- **Untouched (`needs_review`)**: Leave template defaults; candidate stays pending without a fabricated score.
- **Triaged irrelevant (`out_of_scope`)**: Set `triage = {"status": "reviewed", "relevant": false, "abstract_score": <0..5>, "reason": "..."}`. No deep investigation is needed; leave `review_status: "needs_review"` (or set `"complete"` with `open_questions: []`).
- **Excluded stage (`excluded_stage`)**: Triggered automatically when a verified `founder`/`cofounder` has verified `medium`/`high`-confidence dated financing within `financing_max_age_days` in `hard_drop_stages` (`c_plus`, `public`, `acquired`).
- **Scored (`scored`)**: Requires `review_status: "complete"`, explicit `open_questions` list, `triage.relevant: true`, verified `person`, and all four 0–5 assessments with valid scoped evidence.

## Sources by purpose

- Technical claims: paper PDF/HTML methods, experiments and limitations; the linked implementation and its release state. A code link does not prove reproducibility.
- Identity and institution: paper author block, matching personal/lab page, ORCID/OpenAlex as corroboration. Distinguish affiliation at publication from current employment.
- Person-to-company relationship: named founder/team profile, company announcement, or another reliable source identifying both parties and the relationship. Do not transfer an employer's financing to a researcher as if they founded it.
- Financing: dated company/investor announcement or accessible financing database/news record identifying the same company. Keep the original round label and event date; do not treat an old announcement as proof of current stage.
- Team track record: author homepage, organization/lab page, company team page, or database/news record about the person. The paper itself establishes authorship, not track record.
- Contactability: public professional homepage/contact route on an author, organization or company page; documented market/location fit. Do not infer private contact details.

## Unknown and conflicting evidence

Keep `unknown`, `ambiguous`, `not_found`, `inaccessible`, and `stale` distinct. Search failure does not prove absence. Record attempted sources in `evidence` and `reason` with the observed limitation. With conflicting company matches, leave the relationship `ambiguous`. Hard exclusion requires a verified `founder`/`cofounder` relationship and adequate dated stage evidence; low confidence cannot trigger it. Employees, advisors, and research collaborators do not inherit an employer's financing for scoring or exclusion.

`prepare` records a fixed UTC `as_of` date. The default `financing_max_age_days` is `365` (configurable in `scout_rubric.yml`); missing dates, future dates, and older evidence cannot establish a usable current stage, though historical facts remain in the audit. This is a conservative policy, not proof that a company changed stages. Advance `as_of` only with `scout refresh` when deliberately continuing or refreshing research. Profiles may not give a researched, non-excluded stage a multiplier below `unknown`; otherwise skipping financing research would outrank doing it.

Use an initial budget of up to five targeted source lookups per candidate, then one additional query to resolve a decisive contradiction. Adjust to the user's scope. Stop earlier when the evidence is adequate; stop with explicit uncertainty when access or ambiguity prevents a conclusion. Do not repeatedly broaden queries to force an answer.

## Review discipline

`triage.abstract_score` measures whether deeper reading is worthwhile. `assessment.technical` requires full-text/code/independent evaluation evidence (`scope: "full_text"`). Team, thesis fit, and reachability are separate dimensions. Low media visibility is not evidence of being unfunded. Popularity does not lower technical merit.

Leave unreviewed candidates as `needs_review`. A complete candidate records `open_questions` as a list; an empty list is an explicit claim that nothing material remains open. For promising candidates with incomplete facts, provide a research note and explicit unknowns instead of inventing a company or stage. Keep the original paper bundle and source status in the packet so incomplete collection is never silently presented as a complete market map.
