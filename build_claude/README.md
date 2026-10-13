# Medical Record Timeline

Review one patient's scanned medical-record PDF bundles in a combined chronological timeline. Each record has a date, title, summary, and links to its original pages and transcription. The screen has upload progress at the top, a filterable timeline on the left, and a source viewer and editor on the right.

## Quick start

From a clean checkout, with Python 3.11+ and `uv` available:

```bash
cd build_claude
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp env.example .env
# Set OPENAI_API_KEY in .env; keep OPENAI_MODEL=gpt-6-luna.
.venv/bin/python -m app
```

Open http://127.0.0.1:8000 and drop one or more PDFs. No Node build, system OCR packages, authentication, or cloud storage are required. PDFs, images, checkpoints, and edits stay in `MRT_DATA_DIR` (default `build_claude/data`). Page images and record text are sent to OpenAI for model processing.

Standard Python setup also works: run `python3 -m venv .venv`, activate with
`source .venv/bin/activate`, and install with `pip install -r requirements.txt`.
Then copy `env.example` to `.env`, set the key, and run `python -m app`.

Configuration is in [env.example](env.example). Concurrency defaults to **16**, matching the measured build; `MRT_MAX_PAGES=12` limits new development runs. `MRT_FAKE_LLM=1` produces synthetic output for offline checks. It is not used for the reported medical-record evaluation.

## How it works

1. **Render:** PyMuPDF creates a JPEG per page, with a 1600-pixel long side.
2. **Extract:** `gpt-6-luna` transcribes each page and returns typed boundary/date signals. Each completed page is saved immediately.
3. **Group:** deterministic Python rules form record spans and store a readable boundary reason.
4. **Summarize:** text calls summarize each span. Completed summaries are reused when boundaries are unchanged.
5. **Deduplicate:** compare completed records across every uploaded PDF, preserving all source rows.

Grouping starts at page 1 while allowing a partial beginning. Printed page continuity can outweigh changing fax headers or noisy page classifications. A genuine heading and page-number restart can begin another document inside one fax. Otherwise fax changes, headings, and type/date changes guide boundaries. Repeated patient headers and continuation pages stay together. Cover pages attach to the following record.

Exact duplicate detection uses complete page-image containment or identical clinical text. Near candidates require the same known effective date and type and clinical word-set Jaccard similarity **at least 0.85**, followed by a validated model decision. A complete high-confidence duplicate decision or valid `uncertain` decision qualifies; `different`, `distinct`, and `complementary` decisions preserve both records. Failed or incomplete confirmations never activate the fallback. Each member is checked against the canonical record, without transitive merging. Valid decisions, including vetoes, are cached using the source text, effective date/type, model, and prompt. Unchanged inputs reuse the first saved decision across uploads and restarts; changed evidence is checked again.

The longest record is canonical, with document order and page position breaking ties. The detail panel shows each copy's evidence and actual similarity score; the known near pair scores **88.1%**. Click a copies badge to reveal and focus a copy, or enable Show duplicates to reveal every source occurrence. Timeline cards show three lines of summary; the detail editor keeps the full text. Dates display in calendar order without shifting for the browser's time zone.

One background worker processes documents in order; calls within a document run concurrently. SQLite uses WAL and short transactions. Vision is needed because the sample has no text layer. Separating extraction from grouping and summarization permits cached replay. Deterministic IDs derived from document bytes and page spans preserve reviewer edits across retries when boundaries stay the same.

## Features and final evaluation

| Feature | Delivered behavior |
|---|---|
| Editing | Save title, date, type, provider, and summary; edits survive restart; Reset restores model fields |
| PDF removal | Remove a completed or failed PDF after confirmation; its stored copy, pages, records, and edits are removed; remaining duplicates are refreshed |
| Retry | Per-page checkpoints, startup resume, and an injected-failure demo |
| Duplicates | Exact partial copies and near copies collapse; all source pages remain accessible |
| Evaluation | Adopted gold, boundary/date/type/duplicate metrics, date-presence rules, and a claim judge |

Use **Remove** beside a PDF to remove it from this workspace. Cancel leaves it
unchanged. Removal is unavailable while a PDF is queued or processing; wait for it
to finish or fail. Your original file remains on your computer, and other PDFs and
their reviewer edits remain in the workspace. Uploading the removed file again
starts processing it again.

The single canonical gold file is [repository-root eval/gold/record_1.json](../eval/gold/record_1.json); it is the evaluator's default. Derek adopted its 31 records and verified pages 37–41 as one record. The final handoff reports that gold boundaries were independently confirmed by a blind sweep; the stopped review's full synthesis was not available. Labels were not changed to improve scores.

| Metric | Before: baseline completed 14:43 PT | After: final cached replay + fresh judge |
|---|---:|---:|
| Predicted records / gold records | 36 / 31 | 35 / 31 |
| Start precision / recall / F1 | 0.969 / 1.000 / 0.984 | 1.000 / 1.000 / 1.000 |
| Matched gold spans | 30/31: 29 exact + 1 tiled | 31/31: 30 exact + 1 tiled |
| Page assignment | 186/188 | 188/188 |
| Service dates on matched spans | 29/30 | 30/31 |
| Types on matched spans | 30/30 | 31/31 |
| Gold duplicate pairs / false pairs | 0/2 / 0 | 2/2 / 0 |
| Date-rule violations / summaries | 0 / 36 | 0 / 35 |
| Judge supported / total claims | 464/473 (98.1%) | 468/472 (99.2%) |
| Judge unsupported / unclear / call errors | 6 / 3 / 0 | 2 / 2 / 0 |

Reports: [baseline](../eval/results/baseline-14-43.json), [final](../eval/results/final-eval.json). They contain aggregate metrics and hashes; raw clinical claims stay in ignored local data. The final run reused 188 cached pages and one validated real duplicate decision, with **zero new extraction calls**. It made 35 fresh judge calls. An earlier post-fix judge was 496/499 supported; the table reports the latest run without selecting the better result.

There are 35 predictions because four internal starts partition an admission export where gold permits splitting. Thus 31/31 means matched gold spans, including one exact tiling. The judge sees capped transcriptions and key findings, not original images, and its claim counts and labels vary between runs.

## Assumptions, known failures, and cuts

- **One patient per workspace:** every uploaded bundle belongs to the same patient. The app binds to loopback and has no authentication or patient isolation.
- **One known date miss:** r24, pages 151–152, has model date `2024-11-12`; the source visit date is `2024-10-02`. The narrow Epic header is difficult to read at 1600 pixels. Further extraction was explicitly stopped. The editing demo corrects the effective date, verifies persistence after restart, then resets to the original model value. Evaluation remains 30/31.
- **Two unsupported final-judge claims:** spans 157–165 and 185–188 contain an absence claim unsupported by the transcription and a planned action described as completed. These remain known summary errors; original sources are available for review.
- **Failed-page limit:** if any page still fails extraction, grouping does not run for that PDF. Saved pages remain for Retry, but that PDF contributes no timeline records until extraction succeeds. Other completed PDFs remain visible.
- **Source uncertainty:** contradictory laterality, restrictions, historical text, and illegible fields require source inspection. Service dates take priority; report/fax dates can be approximate fallbacks. Birth and injury dates are not substituted for service dates.
- **Changed boundaries:** IDs preserve edits only for unchanged spans. Edits are not automatically transferred to newly split records. A bundle may begin or end mid-record; the 36-page part sample ends inside a source record.
- **Duplicate limits:** exact evidence can identify a subset copy. Near matching requires known dates/types and a bounded model-confirmation pass. Date/type/hidden edits recompute membership. Unchanged evidence reuses its saved model decision; fresh decisions on changed inputs can vary. PT pages 28–31 and 76–78 and complementary operative parts 151–152 and 157–165 remain separate.
- **Explicit demo cache:** both real bundles reuse saved page extraction. The part bundle gets fresh summaries, including its clipped final record. Enable Show duplicates to see both PDFs in combined date order; collapsed copies retain both source links.
- **Cuts:** no schema rewrite, OCR engine, authentication, cloud storage, UI framework, broad fuzzy matching, automatic complementary linking, or new export feature. The existing judge, sort control, source images, and editing/retry flow remain. Next work: difficult date headers, summary grounding, failed-page presentation, and migration of edits when spans change.

## Reproduce verification

From `build_claude`, install development browser tooling only when running browser checks:

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
# Use installed Chrome/Chromium, or: .venv/bin/python -m playwright install chromium
mkdir -p data/checks
MRT_DATA_DIR="$PWD/data/checks" MRT_TEST_SCRATCH_ROOT="$PWD/data/checks" \
  MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q tests
.venv/bin/python -m scripts.clean_checkout_smoke --repo .. --commit "$(git rev-parse HEAD)"
```

The smoke clones the committed candidate, installs its own dependencies with `uv`, uploads two synthetic PDFs, checks source JPEGs and edit/restart/reset, verifies Retry preserves completed pages and summaries, and checks PDF removal across a restart while retaining the other PDFs. Full sample browser checks additionally need `MRT_UI_DATA_SOURCE` pointing to a disposable source copy and `MRT_UI_EXPECT_DUPLICATE_GROUPS=2`.

Evaluate an idle copy of the processed sample, without extraction:

```bash
MRT_DATA_DIR=/Users/dw/indigo-integration/build_claude/data/final/release188 \
  MRT_FAKE_LLM=0 OPENAI_MODEL=gpt-6-luna MRT_CONCURRENCY=16 \
  .venv/bin/python -m eval.run_eval --filename record_1.pdf \
  --gold ../eval/gold/record_1.json --judge --out data/final-eval-raw.json
```

Do not use a store with an active worker. `--judge` makes text-model calls; omit it for deterministic metrics only. [DEMO_EVIDENCE.md](DEMO_EVIDENCE.md) records tested commits and smoke/rehearsal outcomes.

## Eight-step demo for Derek

The prepared clean clone is `/Users/dw/indigo-demo`. Run server commands on the VPS and SSH on the Mac. [DEMO.md](DEMO.md) contains exact start, restart, upload, retry, and evaluation commands.

1. Start the clean-checkout server on port 8000 and open it through the Mac SSH tunnel.
2. Drop `record_1.pdf` and `record_1_part.pdf`; show per-document progress. Explain that extraction is cached and the part bundle receives fresh summaries.
3. Show the combined timeline. Enable Show duplicates to see both PDFs interleaved by date. Open a record's image, transcription, summary, date confidence, and boundary reason.
4. Show 182–184 collapsed under 166–178; show 155–156 under 153–154 with its near-copy score; inspect a cross-bundle copy. Both complementary operative parts remain visible.
5. Edit r24's date to `2024-10-02`, Save, restart the server, and verify persistence. Reset shows the unchanged model date `2024-11-12`.
6. Open the separate synthetic retry server with failure at page 6. Upload its 12-page fixture, show pages 1–5 saved, click Retry in the same process, and show completion without repeating those pages.
7. Show the final eval table and canonical gold. Explain the date miss, allowed internal splits, and two unsupported judge claims.
8. State assumptions and cuts above. The operator must confirm the Mac tunnel; Linux browser verification alone does not establish it works.
