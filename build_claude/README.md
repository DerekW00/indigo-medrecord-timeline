# Medical Record Timeline

Review one patient's scanned medical-record PDF bundles in one chronological timeline. Each record has a date, title, summary, and linked source pages. The screen provides upload progress above the timeline, filters and duplicate visibility, and a source/detail editor.

## Quick start

Run from the repository root. Install `uv` first if it is not available.

```bash
cd build_claude
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp env.example .env
# Set OPENAI_API_KEY in .env; keep OPENAI_MODEL=gpt-6-luna.
.venv/bin/python -m app
```

Open http://127.0.0.1:8000 and drop one or more PDFs. Files, page images, edits, and checkpoints are stored locally in `MRT_DATA_DIR` (default `build_claude/data`). Extraction sends page images to OpenAI; summarization, duplicate confirmation, and optional judging send text to OpenAI. Local storage does not mean offline model processing. Set `MRT_FAKE_LLM=1` for synthetic development and smoke tests; fake output is not a medical interpretation.

`env.example` lists configuration. `MRT_PORT` selects the loopback port; `MRT_CONCURRENCY` defaults to 16; `MRT_MAX_PAGES=12` limits a development run. The demo reuses accepted cached data and does not re-extract the 188-page sample. Never share a data directory between a worker and a replay/eval process.

## How it works

1. PyMuPDF renders pages to JPEG without external OCR tools.
2. A vision call transcribes each page and identifies boundary/date signals. Each completed page is saved immediately in SQLite.
3. Deterministic grouping uses printed page continuity, fax transmissions, headings, record types, and service dates. Cover pages attach forward; mid-record starts receive a flag. Boundary explanations are visible in the detail panel.
4. Text calls summarize grouped transcriptions. Done summaries survive a retry when record boundaries remain unchanged.
5. Duplicate grouping preserves every source record and changes timeline visibility. The measured fixes add exact image-subset matching and high-confidence model confirmation for tightly screened near copies. Final acceptance is reported below.

One background worker processes documents in order; page and summary calls run concurrently. Restart resumes interrupted work. Reuploading identical PDF bytes returns the existing document. IDs derive from PDF bytes and record page spans, so unchanged boundaries preserve reviewer overrides.

## Features and verification

| Feature | Behavior | Release verification |
|---|---|---|
| Editing | Save fields and summary; Reset restores the model version | Integrated restart/UI checks pending |
| Retry | Saved pages/summaries are reused; interrupted work resumes | Synthetic same-process retry and restart checks pending |
| Duplicates | Preserve source records; show canonical cards and copy sources | Final accepted Task3 data pending |
| Evaluation | Fixed human gold, boundary/date/type metrics, date rule, optional claim judge | Final J check pending |

Baseline runtime code: `5048b89f412b8c76fd8d91819469f7fdc8a0d7ac`; lane launch: `7269f0a5ef092b59ae04fee0594d0227c2e3265c`. The baseline measured run also included a live grouping patch, so its results are not attributed solely to the committed runtime. Candidate SHAs and exact evidence are in [DEMO_EVIDENCE.md](DEMO_EVIDENCE.md).

| Metric | Before: saved full188 | After: integrated candidate |
|---|---:|---:|
| Predicted records / human gold | 36 / 31 | Pending |
| Start precision / recall / F1 | 0.969 / 1.000 / 0.984 | Pending |
| False starts | 1 | Pending |
| Matched gold spans | 30/31 (29 exact + 1 permitted tiled) | Pending |
| Page assignment | 186/188 | Pending |
| Service dates on matched spans | 29/30 | Pending |
| Record types on matched spans | 30/30 | Pending |
| Gold duplicate pairs / false pairs | 0/2 / 0 | Pending |
| Date-rule violations / summaries | 0 / 36 | Pending |
| Judge supported / total | 464/473 | Pending |
| Judge unsupported / unclear / call errors | 6 / 3 / 0 | Pending |

The target is 31/31 matched gold spans, including permitted internal partitions, rather than 31 literal predicted spans. Final dates and types must use the actual matched denominator. Judge input is transcription with length limits; judging does not verify every original image or establish clinical truth.

## Assumptions and limits

- All uploaded bundles belong to one patient. There is no authentication or automatic patient isolation; the server binds to loopback for local use.
- The only application model is `gpt-6-luna`. Calls use the existing OpenAI adapter and its validated JSON fallbacks.
- Service dates take priority. Report/fax dates may be approximate fallbacks; birth and injury dates are not service dates.
- A bundle can start mid-record. Fax page 2 with a complete heading may simply follow a removed cover sheet.
- Source conflicts, laterality, restrictions, and uncertain dates remain matters for reviewer inspection. Do not silently resolve contradictory medical evidence.
- Overrides remain attached to deterministic record IDs. A changed boundary creates a different record ID; edits are not automatically transferred to it.
- Synthetic smoke results prove application behavior, not extraction accuracy. Medical artifacts and raw evaluation claims remain ignored and local.
- Scope excludes a new schema, OCR engine, authentication, cloud storage, UI framework, broad regrouping, and evaluator/gold changes. The parked foundation work is excluded.

## Reproduce the checks

```bash
# Offline suite, from build_claude:
mkdir -p data/task5
MRT_DATA_DIR="$PWD/data/task5" MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q --basetemp="$PWD/data/task5/pytest"
# Committed clean clone; creates its own uv environment and synthetic data:
.venv/bin/python -m scripts.clean_checkout_smoke --repo .. --commit "$(git rev-parse HEAD)"
```

The clean-checkout script checks two uploads, timeline/source JPEGs, duplicate upload reuse, edit/restart/persist/reset, and a 12-page synthetic retry. With concurrency 1 and `MRT_FAIL_AT_PAGE=5`, it requires four saved pages before failure, then 12 completed pages after Retry in the same process, with completed results unchanged.

For the release evaluation, use the Task5 SQLite backup of Task3's accepted store, with no server writing it:

```bash
MRT_DATA_DIR="$PWD/data/task5/release188" MRT_FAKE_LLM=0 MRT_CONCURRENCY=16 .venv/bin/python eval/run_eval.py --doc-id d820c2727783 --gold /Users/dw/indigo/eval/gold/record_1.json --judge --out "$PWD/data/task5/post-fixes-eval.json"
```

Task2 alone owns targeted refresh of sample pages 151–152. Other pages reuse cached extraction. The scoped helper commands, provenance, artifact hashes, and actual runs are recorded in [DEMO_EVIDENCE.md](DEMO_EVIDENCE.md); do not run the full sample pipeline for the demo.

## Ten-action demo

1. Open the warmed timeline; on the Mac use the prepared SSH tunnel to the demo server.
2. Show the completed document and its 188 source pages.
3. Open pages 37–41 and show they form one record with a boundary explanation.
4. Open the record covering pages 151–152 and compare its service date with the image.
5. Show the exact subset duplicate and open both source spans.
6. Show the confirmed near duplicate with both sources; toggle Show duplicates.
7. Show the distinct physical-therapy visits and the complementary operative records remain separate.
8. Edit a title, Save, refresh, restart the demo server, verify persistence, then Reset.
9. In the separate synthetic retry tab, show four saved pages, click Retry without restarting, and show 12 completed pages.
10. Open this before/after table and state the remaining limitations and actual acceptance results.

The mandatory Mac walkthrough is not yet verified. Server, tunnel, and rehearsal evidence will be recorded explicitly; an API or browser test does not substitute for the Mac walkthrough.
