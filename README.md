# Medical Record Timeline

A local web app for reviewing scanned medical-record PDFs as one chronological timeline. Each timeline item includes a date, title, summary, and links to the original pages and transcription. The screen has PDF upload and progress controls at the top, a filterable timeline on the left, and a source viewer and editor on the right.

## Quick start

Requires Python 3.11+. From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set OPENAI_API_KEY in .env.
python -m app
```

Alternatively, create and install the environment with `uv`:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env
# Set OPENAI_API_KEY in .env.
.venv/bin/python -m app
```

Open http://127.0.0.1:8000 and drop one or more PDFs. No Node build or system OCR packages are required. PDF files, rendered images, checkpoints, and edits are stored in `data/` by default. Page images and record text are sent to OpenAI `gpt-6-luna` for model processing.

### Configuration

Copy [.env.example](.env.example) to `.env`. Environment variables override that file.

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | Required | OpenAI API key |
| `OPENAI_MODEL` | `gpt-6-luna` | Model used for extraction, summaries, and judging |
| `MRT_DATA_DIR` | `./data` | Local storage directory |
| `MRT_CONCURRENCY` | `16` | Parallel page and summary calls |
| `MRT_MAX_PAGES` | `0` | Page limit; 0 processes every page, 12 is useful for development |
| `MRT_FAIL_AT_PAGE` | `0` | Inject one extraction failure per process for retry testing |
| `MRT_RENDER_LONG_SIDE` | `1600` | Maximum rendered image dimension in pixels |
| `MRT_PORT` | `8000` | Loopback HTTP port |
| `MRT_FAKE_LLM` | `0` | Set to 1 for synthetic offline tests |
| `MRT_EXTRACT_EFFORT` | `low` | Extraction reasoning effort |
| `MRT_SUMMARY_EFFORT` | Empty | Summary reasoning effort; empty uses the API default |

Use a separate data directory for each running server or evaluation process. The app is intended for one reviewer working with one patient's records at a time.

## How it works

1. **Render:** PyMuPDF creates a JPEG for each page.
2. **Extract:** a vision call transcribes the page and returns typed boundary, date, and document signals. Each completed page is saved immediately.
3. **Group:** deterministic rules turn page signals into record spans with readable boundary reasons.
4. **Summarize:** a text call summarizes each record. Completed summaries are reused when the record boundaries are unchanged.
5. **Deduplicate:** completed records are compared across uploaded PDFs while retaining every source occurrence.

Grouping begins at page 1 and allows a partial first record. Printed page continuity can outweigh changing fax headers or noisy page classifications. A new heading and page-number restart can begin another document within one fax. Otherwise fax changes, headings, and type/date changes guide boundaries. Repeated patient headers and continuation pages remain together. Cover pages attach to the following record.

Exact duplicate detection uses complete page-image containment or identical clinical text. Near candidates require the same known effective date and type and word-set Jaccard similarity of at least **0.85**, followed by a validated model decision. High-confidence duplicate decisions and valid `uncertain` decisions qualify; `different`, `distinct`, and `complementary` decisions keep the records separate. Failed or incomplete confirmations do not qualify. Every member is checked against the canonical record, so similarity is not merged transitively.

The longest record is canonical, with document order and page position breaking ties. Duplicate decisions are cached against source evidence, effective date/type, model, and prompt. Unchanged evidence reuses its saved decision across uploads and restarts; changed evidence is checked again.

FastAPI serves the API and vanilla JavaScript UI. One background worker processes PDFs in sequence and processes their pages concurrently. SQLite uses WAL and short transactions. Deterministic record IDs derive from the PDF bytes and page span, preserving reviewer edits across retries when boundaries remain unchanged.

## Features

| Feature | Behavior |
|---|---|
| Combined timeline | All uploaded PDFs sorted by effective date, with source/type filters and ascending/descending order |
| Source inspection | Original page images, transcription, boundary reason, date confidence, flags, and key findings |
| Editing | Save title, date, type, provider, and summary; edits survive restart; Reset restores model fields |
| Retry | Per-page checkpoints and startup resume; completed pages and summaries are reused |
| Duplicates | Collapsed copies retain source links and evidence; click a copies badge to reveal a copy |
| PDF removal | Remove a completed or failed PDF after confirmation; other PDFs and their edits remain |
| Evaluation | Boundary, date, type, duplicate, date-presence, and optional claim-judge metrics |

**Remove** deletes that PDF's stored copy, pages, records, and edits from the workspace. It leaves your original file unchanged. Cancel sends no removal request. Removal is disabled while a PDF is queued or processing. Uploading a removed PDF starts its processing again.

Timeline cards show three lines of summary; the detail editor retains the full text. Calendar dates display consistently across browser time zones. Editing a date or type recomputes duplicate membership without changing the original model value.

## Evaluation

The canonical reference fixture is [eval/gold/record_1.json](eval/gold/record_1.json): 31 records over 188 pages. Its labels are fixed; model errors are not corrected by changing the reference.

| Metric | Baseline | Final |
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

The final evaluation reused 188 cached pages and one previously validated duplicate decision, made no new extraction calls, and made 35 fresh judge calls. Thirty-five predicted spans match 31 gold records because the reference permits four internal starts within a long admission export. One set of predicted spans exactly tiles that gold record.

To process a PDF from the command line:

```bash
.venv/bin/python -m scripts.process samples/record_1.pdf
```

To evaluate an already processed, idle data store without extracting pages again:

```bash
MRT_DATA_DIR=./data .venv/bin/python -m eval.run_eval \
  --filename record_1.pdf --gold eval/gold/record_1.json \
  --out data/eval-results.json
```

Add `--judge` for fresh model-based claim checks. It sends summaries, key findings, and capped source transcriptions to the model. Judge labels and claim counts can vary between runs. PDFs, raw model claims, databases, and generated reports are excluded from Git.

Export predicted spans for human review without replacing the adopted reference:

```bash
.venv/bin/python -m scripts.export_gold --doc-id DOCUMENT_ID --out data/draft-labels.json
```

## Tests

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q tests
.venv/bin/python -m scripts.clean_checkout_smoke --repo . --commit "$(git rev-parse HEAD)"
```

If Chrome or Chromium is already installed, set `MRT_TEST_BROWSER` to its executable instead of installing Chromium. The full-source browser test also requires `MRT_UI_DATA_SOURCE` to point to a completed sample store and `MRT_UI_EXPECT_DUPLICATE_GROUPS=2`; it copies that store before testing. Without the sample, synthetic tests still run and that test is skipped.

The smoke creates a fresh checkout and `uv` environment, then checks synthetic multi-PDF upload, source images, persistent edits, retry, PDF removal, restart persistence, and preservation of other PDFs. It makes no real model calls.

## Demo

1. Start the app with `.venv/bin/python -m app`. For a remote host, forward its loopback port from the Mac with `ssh -N -L 8000:127.0.0.1:8000 USER@HOST`, then open http://127.0.0.1:8000.
2. Drop two PDFs and show their separate progress indicators. Previously completed uploads are reused; a prepared data store also reuses saved extraction.
3. Review their combined timeline. Open a record's summary, page image, transcription, date confidence, and boundary reason.
4. Click a copies badge and inspect its source evidence. In the sample, pages 182–184 are a partial copy of 166–178, and 155–156 are a near copy of 153–154 with 88.1% similarity.
5. Edit r24, pages 151–152, to `2024-10-02`. Restart with the same data directory and verify the edit persists. Reset restores the original model date `2024-11-12`.
6. In a separate server, use `MRT_DATA_DIR=./data/retry-demo MRT_FAKE_LLM=1 MRT_CONCURRENCY=1 MRT_FAIL_AT_PAGE=6 MRT_PORT=8006 .venv/bin/python -m app`. Upload a synthetic PDF of at least six pages, show the saved pages and injected failure, then click Retry without restarting the process.
7. Show the evaluation table and known limitations below. On a disposable completed PDF, demonstrate Cancel and confirmed Remove.
8. Explain the one-patient assumption, local storage, model processing, and the scope deliberately left out.

## Assumptions and known limitations

- **One patient per workspace:** no authentication, patient isolation, or cloud storage. The server binds to loopback. Model processing uses OpenAI.
- **One known date error:** r24, pages 151–152, has model date `2024-11-12` instead of `2024-10-02`. Its narrow header is difficult to read at 1600 pixels. The editing flow corrects the effective date while preserving the original model value; evaluation remains 30/31.
- **Two unsupported judge claims:** summaries for spans 157–165 and 185–188 include an unsupported absence claim and a planned action described as completed. Reviewers can inspect and correct them against the source.
- **Failed-page behavior:** a PDF contributes no timeline records until every page finishes extraction. Completed pages remain saved for Retry, and other PDFs remain usable.
- **Source uncertainty:** contradictory laterality, restrictions, historical text, and illegible fields require inspection. Service dates take priority over report/fax dates; birth and injury dates are not substituted for service dates.
- **Changed boundaries:** edits survive when page spans remain unchanged. They are not automatically transferred to newly split records. Bundles can begin or end mid-record.
- **Duplicate limits:** near matching requires known dates/types and a bounded confirmation pass. Fresh model decisions on changed evidence can vary. Similar but distinct PT visits and complementary operative sections remain separate.
- **Judge limits:** the claim judge sees capped transcription text, not original images; a supported claim is not independent clinical verification.
- **Scope:** no OCR engine, UI framework, schema migration framework, automatic complementary-record linking, or multi-patient workflow. Further work would improve difficult date headers, summary grounding, failed-page presentation, and transfer of edits when boundaries change.
