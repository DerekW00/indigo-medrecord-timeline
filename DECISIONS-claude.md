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

## D3 — Stack
_Pending judge panel. Leading option: all-TypeScript (Node 22, Hono API, Vite + React UI, `mupdf` WASM, `node:sqlite`, `openai` SDK). Single `pnpm install && pnpm dev`. Derek presents TypeScript natively._
