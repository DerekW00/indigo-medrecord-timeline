# Design decisions — Medical Record Timeline

Living log. One entry per decision. Newest at bottom. `PLAN.md` holds the build order and checklist.

## Verified facts (2026-10-13, probed on the build VM)

| Fact | Value | Why it matters |
| --- | --- | --- |
| Fixture `samples/record_1.pdf` | 95 MB, 188 pages, PDF 1.4 | Big: must stream/checkpoint, never hold all renders in memory |
| Text layer | **None on any page** (100% scanned images) | OCR or vision model is mandatory, not optional |
| Content | Workers-comp case, Kaiser Permanente, left ankle trimalleolar fracture, DOI 2024-09-30, ORIF 2024-10-02, P&S report 2025-12-29 | Fax headers (`9/16/2025 4:26:36 PM PDT PAGE 3/005 Fax Server`), Epic chart printouts with `(continued)` and `Generated on` footers, `Visit:` and `Report Date:` fields |
| `gpt-6-luna` | Reasoning model. Chat + Responses API. Image input OK. Strict `json_schema` output OK on both endpoints | One model does OCR + structure + summary |
| Vision OCR, 150 dpi, `detail: high`, `reasoning.effort: low` | ~7-15 s/page, ~2.6K in + ~750 out tokens/page, transcription faithful | 188 pages ≈ 640K tokens per bundle |
| Concurrency | 16 parallel → 17 s wall for 16 pages. Limits: 30K RPM, 180M TPM | Run page stage at concurrency ~24 → ~2 min per bundle |
| Tesseract 5 | 1 s/page, output garbled on headers/logos | Rejected as primary; not used |
| Node `mupdf` (WASM) | Opens 188-page file in 315 ms, renders a page at 150 dpi in 259 ms, byte-identical PNG to PyMuPDF | All-TypeScript stack is viable, no native deps |
| `node:sqlite` (Node 22.23) | Works, prints one ExperimentalWarning | Zero-install persistence |
| VM | Linux, 8 CPU, 31 GB RAM, Node 22, pnpm 10, Python 3.12, uv, Chrome + Playwright present | Demo runs here; UI reached over Tailscale/SSH tunnel |

## D1 — Vision model does OCR, not Tesseract
Tesseract output on these fax scans is unusable (logo noise, dropped headers). `gpt-6-luna` transcribes faithfully and returns page-level structure in the same call. Cost is acceptable (~$ cents per page). **Trade-off:** 2 min per bundle instead of 3 s; mitigated by concurrency and per-page checkpoints.

## D2 — Three checkpointed stages, every unit persisted
`pages` (render → vision OCR → transcript + signals) → `segment` (one LLM call over compact per-page signals → record boundaries) → `summarize` (per record). Each page / segmentation / record row is written to SQLite as soon as it exists. **Consequence:** bonus "Retry resumes from most recent progress" is a property of the design, not a feature to build.

## D3 — Stack: CLAUDE.md baseline (Python FastAPI + PyMuPDF + SQLite + vanilla JS)
Derek's context pack (`CLAUDE.md`, 13:34 PT) fixes the stack. Claude proved the all-TypeScript alternative viable (`mupdf` WASM renders byte-identical PNGs in 259 ms; `node:sqlite` works) but switching costs time and the pack's prompts, lanes and README outline all assume Python. **Not recommended to switch.**

## D4 — Fax identity is computed in code from the verbatim header, never from a model-normalized field
Full-pass evidence: the model wrote `10/30/2025 3:17:27 PM`, `2025-10-30 3:17:27 PM` and `2025-10-30T15:17:27-07` for pages of one fax. Naive equality produced 78 spans for a 31-record bundle (pages 37–41 alone carried four spellings of one timestamp). Canonicalize to digits; fall back to `fax_page.n` equal and `k == prev.k + 1`.

## D5 — Near-duplicates are advisory; exact duplicates may collapse
Known near-dup (153–154 vs 155–156) scores 0.64 naive Jaccard; distinct PR-2 pages score up to 0.74 on boilerplate. A fixed 0.7 threshold both misses and over-matches. Exact `content_hash` match collapses with a `2 copies` badge; `similar` is a badge plus a list, and requires equal service dates (DESIGN_REVIEW R5).

## D6 — Concurrency 16, full runs are cheap
188 pages in 85 s at 24 parallel, no 429s; 744K tokens per bundle. Run the full bundle after every pipeline change instead of "at most twice".

## D7 — Whole-record summarization, hard cap with a visible flag
Longest record is 110K chars (~27K tokens), within one call. Cap at 400K chars and flag `summary incomplete`; never drop the middle silently (DESIGN_REVIEW R3).

## D8 — Product-intent answers (Derek, 14:02 PT)
1. **Fax cover sheets are suppressed.** They attach to the record that follows (CLAUDE.md §7 post-processing) and never produce a timeline card; the detail panel still shows the page.
2. **Duplicate copies are indicated, not hidden.** Every copy stays on the timeline with a `duplicate of <record>` badge and a link to the canonical card; a `Collapse duplicates` toggle (off by default) hides non-canonical copies. Replaces CLAUDE.md §8 "canonical card + Show duplicates toggle".
3. **One patient per bundle is guaranteed.** Multi-patient banner dropped from the stretch list; `patient_name` stays in `PageSignals` for the eval only.
