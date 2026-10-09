# Evidence protocol

Research Radar keeps collection, research judgments, and report rendering separate. A complete packet means the work and uncertainty are recorded, not that every field is known.

## Evidence entries

Use the `scout prepare` output as the schema template. Each citation needs a unique ID, a public HTTP(S) URL of the page actually checked (placeholder, local and private hosts are rejected), checked date, source date when available, source kind, applicable entity IDs, and a concise note describing the supported fact. Quote only the short relevant passage when useful. A homepage URL with no observation is not a verified claim.

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

Allowed `kind` values: `paper_full_text`, `paper_abstract`, `code`, `independent_evaluation`, `author_homepage`, `organization`, `company`, `funding_announcement`, `database`, `news`, `other`. Reference an entry's `id` from the appropriate `evidence_ids` list. `entity_ids` must bind the evidence to the exact paper/person/company IDs used in the packet.

For a verified person, at least one entry cited in `person.evidence_ids` must bind both that person's ID and the candidate's paper ID. The note should describe the author-to-paper match. The person's name must also match the collected byline, directly or via `person.listed_name`; with `--per-author`, it must match the byline position in `author_index`. Use the paper author block or a matching professional page; do not add entity IDs to a source that does not establish the relationship. All evidence must have `checked_at` on or before `as_of`, and a non-null `source_date` on or before `checked_at`. `prepare` sets `as_of` and `prepared_at` to the same UTC day. To continue research later, run `scout refresh` to move `as_of` forward (the old value is kept in `as_of_history`), then add evidence with its true `checked_at`. Recheck dated claims when you refresh.

Allowed relationship types: `founder`, `cofounder`, `employee`, `advisor`, `research_collaborator` (leave null when unknown). Default financing stages: `unknown`, `bootstrapped_confirmed`, `pre_seed`, `seed`, `angel`, `pre_a`, `pre_a_plus`, `a`, `a_plus`, `b`, `c_plus`, `public`, `acquired`. Confidence is `low`, `medium`, or `high`. See the synthetic review for a complete packet; its values are test fixtures, not reusable evidence.

Sources by purpose:

- Technical claims: paper PDF/HTML methods, experiments and limitations; the linked implementation and its release state. A code link does not prove reproducibility.
- Identity and institution: paper author block, matching personal/lab page, ORCID/OpenAlex as corroboration. Distinguish affiliation at publication from current employment.
- Person-to-company relationship: named founder/team profile, company announcement, or another reliable source identifying both parties and the relationship. Do not transfer an employer's financing to a researcher as if they founded it.
- Financing: dated company/investor announcement or accessible financing database/news record identifying the same company. Keep the original round label and event date; do not treat an old announcement as proof of current stage.
- Team track record: author homepage, organization/lab page, company team page, or database/news record about the person. The paper itself establishes authorship, not track record.
- Contactability: public professional homepage/contact route on an author, organization or company page; documented market/location fit. Do not infer private contact details.

## Unknown and conflicting evidence

Keep `unknown`, `ambiguous`, `not_found`, `inaccessible`, and `stale` distinct. Search failure does not prove absence. Record attempted sources in notes with the observed limitation. With conflicting company matches, leave the relationship ambiguous. Hard exclusion requires a verified founder/cofounder relationship and adequate dated stage evidence; low confidence cannot trigger it. Employees, advisors, and research collaborators do not inherit an employer's financing for scoring or exclusion.

`prepare` records a fixed UTC `as_of` date. The default `financing_max_age_days` is 365; missing dates, future dates, and older evidence cannot establish a usable current stage. Historical facts remain in the audit. This is a conservative configurable policy, not proof that a company changed stages. Advance `as_of` only with `scout refresh`, when deliberately continuing or refreshing the research. Profiles may not give a researched, non-excluded stage a multiplier below `unknown`; otherwise skipping a financing check would outrank doing it.

Use an initial budget of up to five targeted source lookups per candidate, then one additional query to resolve a decisive contradiction. Adjust to the user's scope. Stop earlier when the evidence is adequate; stop with explicit uncertainty when access or ambiguity prevents a conclusion. Do not repeatedly broaden queries to force an answer.

## Review discipline

`abstract_score` measures whether deeper reading is worthwhile. `assessment.technical` describes the evidence actually examined and must name its scope. Team, thesis fit, and reachability are separate dimensions. Low media visibility is not evidence of being unfunded. Popularity does not lower technical merit.

Leave unreviewed candidates as `needs_review`. A complete candidate records `open_questions` as a list; an empty list is an explicit claim that nothing material remains open. For reviewed but irrelevant papers, record the triage reason. For promising candidates with incomplete facts, provide a research note and explicit unknowns instead of inventing a company or stage. Keep the original paper bundle and source status in the packet so incomplete collection is never silently presented as a complete market map.
