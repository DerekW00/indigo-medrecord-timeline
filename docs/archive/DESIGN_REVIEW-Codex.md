# Medical Record Timeline: design review — Codex

Status: proposed corrections, 2026-10-13. Reviewed the complete [CLAUDE.md](CLAUDE.md) context pack with an independent GPT-6 Astra reviewer. These are defects or gaps in the written design, not failures reproduced in application code. CLAUDE.md was not modified. The claimed 31-record result and full gold labels were not independently verified in this session.

Provenance: this is Codex's critique of CLAUDE.md, informed by local fixture inspection and an independent Codex review worker. It is not a blind architectural proposal. Author: Codex. All recommendations remain separate from the other AIs' outputs until comparison and reconciliation.

The stack is appropriately small for the build window. Resolve the first four findings before relying on the pipeline; resolve the duplicate finding before enabling automatic near-duplicate hiding. P1 means a core correctness or recovery risk; P2 means a narrower contract defect.

## 1. Corrections to make before implementation depends on them

### R1 — P1: a 12-page run can prevent completion of the full document

**References:** CLAUDE.md sections 4, 6, and 13 (`MRT_MAX_PAGES`, duplicate upload handling, and the small-then-full run sequence).

The document ID is the hash of the full file, and duplicate uploads return the existing document without processing. A 12-page development run has the same ID as a full 188-page run. The design does not distinguish a deliberately capped run from a finished document.

**Proposed correction:** persist the original page count and the requested processing scope. Mark a capped result visibly incomplete. Only mark the document `done` when all original pages and their records are accounted for. Expanding the scope schedules unprocessed work. Keep this state explicit in the API and UI contract.

**Acceptance:** process 12 pages; then lift the cap in the same data directory. The remaining 176 pages are eligible for processing, and successful unchanged page extraction is reused. Any record cut off at page 12 stays partial until its continuation is processed.

### R2 — P1: regrouping deletes completed summaries

**Reference:** CLAUDE.md section 6, especially “Grouping deletes and recreates the records rows.”

On restart during summarization, rerunning grouping can delete completed summaries and their `done` states. Deterministic IDs preserve links to overrides; they do not preserve deleted generated content. This conflicts with the promise that completed units are skipped.

**Proposed correction:** reconcile spans transactionally using stable IDs. Preserve an existing summary only when its source content and relevant extraction/grouping/summary versions are unchanged. Insert new spans; retire superseded ones without cascading away user edits. Ordinary retry must not regroup a completed unchanged grouping stage. If a boundary changes, retain the old override visibly for manual reconciliation instead of silently discarding or misapplying it.

**Acceptance:** let several summaries finish, fail another, restart, and retry. Finished summaries incur no new fake-model calls, records appear once, and existing edits remain. A changed source span invalidates only dependent output.

### R3 — P1: long-record summaries silently omit the middle

**References:** CLAUDE.md section 6 summary input cap and Appendix B.

Keeping the first 30,000 and last 8,000 characters drops the middle of a long record. A card still cites the full source span, and its required flags do not disclose that omission. The context pack explicitly anticipates long chart printouts.

**Proposed correction:** use all source text when it fits the measured request budget. Otherwise summarize bounded, page-labeled chunks and synthesize from all of them, retaining the chunk evidence. If that cannot be completed, show a visibly incomplete summary with the omitted page scope. Do not discard the middle and call the result complete.

**Acceptance:** a synthetic long record contains its key treatment change only in the middle. The resulting summary includes supported information from that region or is explicitly marked incomplete. Evaluation checks original source images, not only the generated transcript.

### R4 — P1: fax continuity can merge distinct same-day records

**Reference:** CLAUDE.md section 7, rule 3.

Within a continuous fax, the rule requires a type or date change to start a new record. Two separate records of the same type and date therefore merge even when a new form clearly starts and printed page numbering resets. Missing dates can cause the same result.

**Proposed correction:** permit strong independent start evidence, such as a new-document heading plus a printed page reset, to override fax continuity without requiring a type/date change. Retain the protection against repeated page headers. Treat these signals as fallible and expose the boundary reason.

**Acceptance:** two same-type, same-date forms in one fax become two records; a repeated letterhead across one multi-page form remains one record. Also test continuing printed labels when a fax transmission changes.

### R5 — P1: transitive near-duplicate groups can hide different records

**Reference:** CLAUDE.md section 8.

The candidate rule permits a pair when either date is null, and union-find merges matching pairs transitively. Record A with one date can match undated B; B can match C with a different date. The final group hides A or C even though their dates disagree. Boilerplate-heavy forms also make a fixed similarity threshold risky without evidence.

**Proposed correction:** begin with exact duplicate handling. Keep near matches visible as candidates until validated. If automatic near grouping is retained, prohibit conflicting known dates and require every member to qualify directly against the chosen canonical record. Preserve all source occurrences and edit visibility. Do not describe normalized-text equality as verified image identity.

**Acceptance:** test the A–B–C bridge, two distinct same-date forms, and an exact repeated source. Uncertain pairs remain visible; exact duplicates expose all source references. A shorter partial copy must not cause unique pages of a longer record to disappear.

### R6 — P2: `other_dates` has no usable strict object schema

**References:** CLAUDE.md section 6 `PageSignals` and Appendix A.

`other_dates: list[dict]` does not declare the requested `date` and `label` properties. Recursively setting `additionalProperties: false` forbids those properties rather than defining them. Falling back to loose JSON conceals the schema defect.

**Proposed correction:** define a typed `OtherDate` model with explicit fields. Generate and validate the full schema before the first real model probe. Handle malformed, incomplete, or refused responses as failed units; a schema-conforming response is still not evidence that the content is correct.

**Acceptance:** a synthetic response with a valid date/label pair validates; malformed entries fail. The first real `gpt-6-luna` image probe successfully returns the expected typed structure. [Official structured-output guidance](https://developers.openai.com/api/docs/guides/structured-outputs)

## 2. Clarifications for the API contract and demo

- **Date meaning:** the baseline substitutes a report/fax date for a missing service date. Preserve the date's source and show its basis; a `~` marker alone does not explain that it is a transmission date. Prefer unknown service date to an unlabeled substitute. Do not turn a month-only date into an invented exact day.
- **Record granularity:** one item is one source record. A historical event mentioned in a later report is not automatically another record. The current whole-page span model cannot separate two independent records sharing one page; label that limitation unless support is added. The sampled pages did not establish how often that case occurs.
- **Concurrent recovery:** with eight parallel calls, “resume at page 91” is not an accurate promise. Resume every unfinished page; successful pages may be out of order. Deduplicate queued/running document IDs. The failure-injection hook must not repeatedly fire after restart if the demo claims it fires only once.
- **Coverage versus quality:** all 188 pages reaching a terminal state proves accounting, not correct boundaries or faithful transcription. The completion screen and evaluation must distinguish failed pages, reviewed records, and unreviewed generated output.
- **Evaluation labels:** a model-exported span file becomes gold only after independent source review. The assertion that someone is producing all gold labels is not verified here. Use an honestly scoped subset rather than self-evaluation presented as ground truth.
- **Local storage versus model processing:** files and results are stored locally, but page images/text are sent to OpenAI for model calls. Correct the extraction prompt's statement that the data is “processed locally”; do not present the app as offline.
- **Fixture measurements:** the scan dimensions vary. Local inspection found 12 page-size groups, including 2550 × 3301 and 2542 × 3496 raster dimensions; the one-size description in CLAUDE.md is not universal. This does not alter the page-rendering approach.

## 3. Review decision

Recommended: keep the stack and per-page extraction design; adopt R1–R4 and R6 before the parallel implementation contract is frozen. Keep near duplicates advisory until R5 has a passing check. Preserve original source images in the core UI.

Review now: check the source-coverage, checkpoint, and boundary risks in R1–R4; flag factual corrections. The recommendations remain proposals for the later diversity review and reconciliation. No application tests, full model run, or end-to-end behavior are claimed by this review.
