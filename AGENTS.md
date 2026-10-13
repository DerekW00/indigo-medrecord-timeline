# Medical Record Timeline: context pack

Read this file in full before you write code. It holds every decision for this build so you do not re-derive them. The decisions are final unless they block you. If they block you, make the smallest change that unblocks you, and write the assumption into the README section "Assumptions".

## 1. The task (verbatim from the brief)

> Build a local web app for reviewing medical records.
>
> A user drags one or more medical-record PDFs into it. Each PDF is a *bundle*: it contains many separate records stacked back to back (an office visit, then an MRI report, then a physical-therapy note, then another office visit).
>
> The output is a single sorted, combined timeline of those individual records across **all** the PDFs the user dropped in, so a reviewer can read the patient's medical history in chronological order instead of paging through hundreds of unsorted scans. Each timeline item is one record, summarized at a high level: a title, a date, and summarized content, with a reasonable boundary around what belongs to that record. Formatting is up to you.

Bonus points, in priority order for this build:

1. Editing: a user can edit an item's fields and summary text, and the edits are still there after the app restarts.
2. Retry: a PDF that fails partway through can be retried and picks up from its most recent progress instead of starting over.
3. Duplicates: check if the same or a similar record appears more than once, and dedupe on the timeline.
4. Eval: how do you verify record boundaries and summaries are right?

## 2. Hard constraints

- Half-day build. Code freeze at 16:30 PT today. Working features only. A half-built feature is worth zero. Cut scope before you cut quality.
- Deliverable: a repo that runs from a clean checkout with a README, plus a live demo that Derek drives.
- Model: OpenAI `gpt-6-luna`, key in env var `OPENAI_API_KEY`. No other model, no other provider.
- Local, single user, no auth, no cloud storage.
- Sample PDFs live in `samples/` (gitignored, they are large). The known sample is `samples/record_1.pdf`.
- Python 3.11+. No Node build step. No system packages beyond Python (no tesseract, no poppler).

## 3. What the sample bundle looks like (observed 13:05 PT)

- `record_1.pdf`: 188 pages, 95 MB, PDF 1.4. Every page is one 300 DPI JPEG scan (2542 x 3386 px). There is no text layer at all. `pdffonts` returns nothing. Text extraction returns nothing. A vision model or OCR is required.
- Content: a California workers' compensation case. Kaiser Permanente "on-the-job" documents: PR-2 progress reports (DWC form), a PR-4 permanent and stationary report, physical therapy notes, a hospital admission with implant logs (ORIF of a left ankle, screws and plates), radiology orders and reports. Patient identifiers are redacted with black boxes.
- Boundary cues are strong and mostly in the margins:
  - Fax transmission headers at the top: `10/30/2025 3:17:27 PM PDT  PAGE 2/005  Fax Server`, or `Dec/29/2025 2:01:57 PM  Kaise[r]  2/6`.
  - A vertical red stamp on the left edge: `Received by TPA ICS Fax 10/30/25`.
  - Form footers: `DWC Form PR-2 (10/2015)  Page 1`, `Page 2`.
  - Letterheads and form titles: `KAISER PERMANENTE on-the-job`, `PRIMARY TREATING PHYSICIAN'S PROGRESS REPORT (PR-2)`.
  - Kaiser chart printouts repeat a small patient header on every page (`MRN ... DOB ... Visit date ...`) and show `(continued)` in section titles.
- The order inside the bundle is roughly reverse chronological (Dec 2025 first, then Oct 2025, Sep 2025, Jan 2025, Oct 2024).
- Page 1 of the bundle is page 2 of 6 of a fax. A bundle can start in the middle of a record. The pipeline must not assume that page 1 starts a record.
- Fax cover pages were stripped before scanning: almost every faxed record starts at fax page 2 of N. "Fax page k > 1" alone does not mean a partial record.
- Known structures in the sample (from a local OCR pass, use them as test targets):
  - 31 records. Most are 4 to 6 page DWC PR-2 progress reports, one fax transmission each.
  - Two long Kaiser chart printouts: pages 92 to 148 (a 10/02/2024 admission with H&P, operative report, anesthesia, implant logs, labs) and pages 166 to 178 (a 09/30/2024 emergency visit). Every page repeats the patient header and the first section title says "(continued)". Each printout is one record; splitting it into its notes is acceptable.
  - A PR-2 whose fax transmission changes mid-record while the printed page labels continue (pages 79 to 83: "Page 2" then "Page 3" of the form, fax header changes). The page label rule must win over the fax id rule there.
  - Duplicates inside the one bundle: pages 153 to 154 and 155 to 156 are the same 2-page note printed twice (near duplicate); pages 182 to 184 are an exact re-scan of pages 166 to 168 (a partial duplicate of a 13-page record).
  - A 25-page fax (KPRFDT001) that holds several documents: an X-ray report (pages 149 to 150), an operative report (151 to 152), a discharge note (153 to 154). Fax page numbers run across documents, so a new record inside a fax is real.

## 4. Architecture

Four parts:

1. Web UI: one static page (`static/index.html`, `static/app.js`, `static/style.css`). Vanilla JS, `fetch`, polling every 1.5 s. Drop zone, per-PDF progress, the timeline, a detail panel with page images and transcriptions, an inline editor.
2. API server: FastAPI + uvicorn. Serves the static page, the API, and the page images.
3. Pipeline: four stages per PDF (render, extract, group, summarize), then dedupe across all PDFs. One in-process async worker processes documents one at a time; pages inside a document run in parallel.
4. Store: SQLite in `data/app.db` (WAL mode). Page images in `data/pages/<doc_id>/<page_no:04d>.jpg`. Every stage writes its progress to SQLite per unit (per page, per record). The UI reads only from the store.

Module layout (keep files small and single purpose):

```
app/
  __main__.py        # python -m app  -> uvicorn on 127.0.0.1:8000
  config.py          # env vars with defaults
  db.py              # schema, connection, small DAO functions
  api.py             # FastAPI routes
  worker.py          # queue + resume on startup + stage runner
  llm.py             # OpenAI adapter: vision_json(), text_json(), retries
  pipeline/
    render.py        # PyMuPDF -> JPEG per page
    extract.py       # per-page vision call -> PageSignals
    group.py         # pure function: pages -> record spans (unit tested)
    summarize.py     # per-record text call -> RecordSummary
    dedupe.py        # exact + near duplicate detection (unit tested)
  prompts/
    page_extract.md
    record_summary.md
    summary_judge.md
static/
scripts/
  process.py         # CLI: run the pipeline on a PDF without the UI
  export_gold.py     # dump a document's current record spans as a gold file
eval/
  run_eval.py
  gold/record_1.json
tests/
  test_group.py
  test_dedupe.py
```

Environment variables (`app/config.py`, all with defaults, documented in `.env.example`):

- `OPENAI_API_KEY` (required)
- `OPENAI_MODEL` default `gpt-6-luna`
- `MRT_DATA_DIR` default `./data`
- `MRT_CONCURRENCY` default `8` (parallel page calls)
- `MRT_MAX_PAGES` default `0` = all. Set to `12` during development so a run takes seconds, not minutes.
- `MRT_FAIL_AT_PAGE` default `0`. If set, the extract stage raises once at that page (first attempt only). Demo hook for the retry feature.
- `MRT_RENDER_LONG_SIDE` default `1600` px.
- `MRT_PORT` default `8000`. Each git worktree runs on its own port.

## 5. Data model (SQLite)

```sql
documents(
  id TEXT PRIMARY KEY,           -- sha256 of the file bytes, hex
  filename TEXT, page_count INTEGER,
  status TEXT,                   -- queued|rendering|extracting|grouping|summarizing|deduping|done|failed|interrupted
  stage_done INTEGER, stage_total INTEGER,
  error TEXT, created_at TEXT, updated_at TEXT
)
pages(
  doc_id TEXT, page_no INTEGER,  -- 1-based
  image_path TEXT,
  status TEXT,                   -- pending|done|failed
  attempts INTEGER, error TEXT,
  signals_json TEXT,             -- PageSignals as JSON (see section 6)
  text TEXT, text_hash TEXT,     -- transcription and sha256 of normalized text
  PRIMARY KEY(doc_id, page_no)
)
records(
  id TEXT PRIMARY KEY,           -- sha256(doc_id + ':' + start_page + ':' + end_page)[:16], deterministic
  doc_id TEXT, start_page INTEGER, end_page INTEGER,
  boundary_reason TEXT,          -- why this span starts here, human readable
  partial_start INTEGER,         -- 1 if the record starts mid-document (fax page k>1, page label k>1)
  status TEXT,                   -- pending|done|failed
  error TEXT,
  record_type TEXT, title TEXT, date_of_service TEXT, date_confidence TEXT,
  provider TEXT, facility TEXT, summary TEXT, key_findings_json TEXT, flags_json TEXT,
  content_hash TEXT,             -- sha256 of normalized concatenated page text
  created_at TEXT, updated_at TEXT
)
record_overrides(
  record_id TEXT PRIMARY KEY,    -- user edits live here, never in records
  title TEXT, date_of_service TEXT, record_type TEXT, provider TEXT, summary TEXT,
  hidden INTEGER DEFAULT 0, updated_at TEXT
)
duplicate_groups(
  record_id TEXT PRIMARY KEY, group_id TEXT, is_canonical INTEGER,
  reason TEXT,                   -- exact|similar
  score REAL
)
```

Why deterministic record ids: a re-run of a document regenerates records. If the boundaries come out the same, the ids are the same and the user's overrides survive. Say this at the demo.

Effective record = `records` fields with `record_overrides` fields applied on top. The API returns both the effective value and the model value so the UI can show "edited" and offer "reset to model version".

## 6. Pipeline

All stages are idempotent and resumable. Each stage skips units that are already `done`.

### Stage 1: render

PyMuPDF (`pymupdf`, imports as `fitz`). For each page without an image file: render so the long side is `MRT_RENDER_LONG_SIDE` px, RGB, no alpha, JPEG quality 80. Run the rendering in `asyncio.to_thread` so the event loop stays free. Insert a `pages` row with status `pending`.

```python
zoom = long_side / max(page.rect.width, page.rect.height)
pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
pix.save(path, jpg_quality=80)
```

### Stage 2: extract (one vision call per page)

For each page with status != `done`: send the JPEG as a base64 data URL with the prompt in `app/prompts/page_extract.md` (Appendix A) and parse the JSON into `PageSignals`:

```python
class FaxPage(BaseModel):   k: int; n: int | None
class PageSignals(BaseModel):
    transcription: str
    fax_header: str | None
    fax_page: FaxPage | None
    page_label: FaxPage | None          # printed "Page 2 of 5" style numbering
    starts_new_record: bool
    boundary_confidence: Literal["high", "medium", "low"]
    doc_type: Literal["progress_report", "permanent_stationary_report", "office_visit", "consultation",
                      "operative_report", "hospital_record", "imaging_report", "imaging_order",
                      "physical_therapy", "lab_result", "medication", "work_status",
                      "correspondence", "cover_sheet", "billing", "form_other", "other"]
    date_of_service: str | None         # YYYY-MM-DD
    other_dates: list[dict]             # {"date": "YYYY-MM-DD", "label": "fax date|report date|date of injury|dob|other"}
    provider: str | None
    facility: str | None
    patient_name: str | None
    gist: str
    is_blank_or_cover: bool
```

Concurrency: `asyncio.Semaphore(MRT_CONCURRENCY)`, `asyncio.gather` over the pending pages. Save each page the moment its call returns. Update `documents.stage_done` after each page so the UI shows `extracting 57/188`.

Failure policy: 3 attempts per page with exponential backoff (2 s, 4 s, 8 s) for timeouts, 429 and 5xx. After 3 failures the page is `failed` and the document continues. At the end of the stage, if any page is `failed`, the document is `failed` with the error `N pages failed, retry to resume`. Retry re-runs only the failed and pending pages.

### Stage 3: group (pure function, no model)

`group_pages(signals: list[PageSignals]) -> list[RecordSpan]` in `app/pipeline/group.py`. Unit tested with synthetic sequences. Rules in section 7.

Grouping deletes and recreates the `records` rows of the document. Because ids are deterministic, unchanged spans keep their id, and `record_overrides` are untouched.

### Stage 4: summarize (one text call per record)

For each record with status != `done`: concatenate the page transcriptions with markers `=== Page 12 ===`. Cap the input at about 40,000 characters. If longer, keep the first 30,000 and the last 8,000 characters and insert `[... middle omitted ...]`. Call the model with `app/prompts/record_summary.md` (Appendix B). Parse into:

```python
class RecordSummary(BaseModel):
    title: str                           # <= 80 chars
    record_type: str                     # same enum as PageSignals.doc_type
    date_of_service: str | None
    date_confidence: Literal["exact", "approximate", "unknown"]
    provider: str | None
    facility: str | None
    summary: str                         # 3 to 6 sentences, high level
    key_findings: list[str]              # <= 5 short items
    flags: list[str]                     # "partial record", "illegible sections", "multiple dates"
```

Date rule: the service date wins. If the pages carry no service date, use the report or fax date and set `date_confidence = approximate`. Never use date of birth or date of injury as the service date.

Records run in parallel with the same semaphore. Save each record the moment its call returns.

### Stage 5: dedupe (across all documents)

Runs after every document reaches `done`, over all `done` records of all documents. See section 8.

### Worker and resume

- One `asyncio.Queue` of document ids. One worker task started on app startup.
- On startup, every document whose status is not `done` or `failed` is marked `interrupted` and re-enqueued. Stages skip done units, so a server restart at page 90 resumes at page 91.
- `POST /documents/{id}/retry` sets status `queued` and enqueues. Nothing is deleted.
- A document dropped twice (same sha256) is not re-processed. The API returns the existing document.
- `MRT_FAIL_AT_PAGE=40`: the extract stage raises `RuntimeError("injected failure")` the first time it reaches page 40 in this process. The document goes to `failed` with pages 1 to 39 saved. Retry resumes at page 40. This is the retry demo.

## 7. Grouping rules (the core logic)

Inputs per page: `starts_new_record`, `boundary_confidence`, `fax_header`, `fax_page {k, n}`, `page_label {k, n}`, `doc_type`, `date_of_service`, `is_blank_or_cover`.

Derived: `fax_id` = the fax header with the page numbers removed and whitespace collapsed (for example `10/30/2025 3:17:27 PM PDT Fax Server`). Two pages belong to the same fax transmission when their `fax_id` values are equal.

Walk pages 1 to N in order. Page 1 always starts record 1.

For page i (i > 1), with `prev` = page i-1:

1. `same_fax` = `fax_id` is not null, equals `prev.fax_id`, and `fax_page.k == prev.fax_page.k + 1`.
2. `label_continues` = `page_label.k == prev.page_label.k + 1` and `doc_type == prev.doc_type`.
3. If `same_fax` or `label_continues`:
   new record only if `starts_new_record` is true with `high` confidence AND (`doc_type != prev.doc_type` OR both dates are present and differ). Reason: `new document inside the same fax`. Otherwise continuation. This rule stops letterheads that repeat on every page from splitting a record.
4. Else if `fax_page.k == 1`: new record, reason `fax restart 1/N`.
5. Else if `fax_id` and `prev.fax_id` are both present and differ: new record, reason `fax transmission changed`.
6. Else if `starts_new_record`: new record, reason `new letterhead or form title` (append the confidence).
7. Else if `prev.doc_type != doc_type` and `boundary_confidence != low` and both dates are present and differ: new record, reason `type and date changed`.
8. Else: continuation.

Post-processing:

- A `cover_sheet` or blank page attaches to the record that follows it (it is the first page of the next record), unless it is the last page.
- `partial_start = 1` when the first page of a record has `page_label.k > 1`, or has `fax_page.k > 1` and `starts_new_record` false. A stripped fax cover page (fax page 2 with a full heading) is not a partial record.
- Inside one fax transmission, a page with `starts_new_record` high confidence and a different `doc_type` than the previous page starts a new record (rule 3). The 25-page fax in the sample holds an X-ray report, an operative report and a discharge note back to back.
- `boundary_reason` is stored on the record and shown in the detail panel. It is the explanation at the demo and the first thing to look at when the eval disagrees.

Write `tests/test_group.py` with at least these cases: a clean fax of 5 pages; two faxes back to back; a bundle that starts at page 2/6; letterhead repeated on every page of one fax; a cover sheet before a record; a type and date change without any fax header; a fax id change while page labels continue (pages 79 to 83 of the sample); two documents inside one fax (pages 149 to 154 of the sample); a chart printout with "(continued)" pages.

## 8. Duplicates

Normalize text: lowercase, drop the fax header line and the TPA stamp text, keep only `[a-z0-9 ]`, collapse whitespace.

- Exact: same `content_hash` of the normalized concatenated text, across any documents.
- Near: candidate pairs share the same effective `date_of_service` (or one is null) and the same `record_type`. Score = Jaccard similarity of word 3-gram shingles of the normalized text. Duplicate if score >= 0.7. Union-find the pairs into groups.
- Canonical record of a group: the one with the most pages, then the earliest document. The timeline shows the canonical card with a badge `2 copies` and lists the other sources. A toggle `Show duplicates` reveals the others. Overrides on a non-canonical record stay with that record.

`tests/test_dedupe.py`: identical text with different fax headers is exact; text with a few transcription differences is near; different records with the same date are not duplicates.

## 9. API

```
POST   /api/documents                 multipart upload, one or more PDFs -> [{id, filename, status}]
GET    /api/documents                 list with status, stage_done, stage_total, error, page_count
POST   /api/documents/{id}/retry
DELETE /api/documents/{id}            optional, only if time allows
GET    /api/timeline?include_duplicates=0&type=&doc_id=
       -> records sorted by effective date asc, undated last, then doc order and start_page.
          Each item: id, doc_id, filename, start_page, end_page, effective fields, model fields,
          edited (bool), partial_start, boundary_reason, duplicate {group_id, is_canonical, copies, others[]}
GET    /api/records/{id}              item + pages [{page_no, image_url, transcription, gist}]
PATCH  /api/records/{id}              body: any of title, date_of_service, record_type, provider, summary, hidden
                                      -> writes record_overrides, returns the item
DELETE /api/records/{id}/overrides    reset to model version
GET    /pages/{doc_id}/{page_no}.jpg  static file
GET    /                               the UI
```

## 10. UI

One page, three regions:

- Top bar: drop zone (also a file input), list of documents with a progress bar, stage name, `57/188`, a Retry button when `failed` or `interrupted`, and the error text.
- Timeline (left, scrolling): month headers, then cards. A card shows date (with `~` when approximate, `undated` when unknown), title, type chip, provider, source badge `record_1.pdf p. 12 to 14`, `partial` chip, `2 copies` badge, the summary. Filters above the list: type, source PDF, show duplicates. Sort toggle asc or desc.
- Detail panel (right): opens on card click. Shows the effective fields in an inline edit form with Save and Reset buttons, `boundary_reason`, key findings, flags, then the page images (lazy loaded, click to open full size in a new tab) with the transcription under each image.

Keep the CSS small and readable. System font stack, 1 accent color, clear hierarchy. No framework.

## 11. Bonus features, in build order

1. Editing: `record_overrides`, `PATCH`, `edited` chip, reset. Survives restart because it is in SQLite.
2. Retry: resume logic in section 6, `MRT_FAIL_AT_PAGE` demo hook, startup resume.
3. Duplicates: section 8.
4. Eval: section 12.

## 12. Eval

`eval/run_eval.py --doc-id <id> [--judge]`:

Boundaries, against `eval/gold/<filename>.json`:

```json
{
  "filename": "record_1.pdf",
  "labeled_pages": [1, 188],
  "records": [
    {"id": "r01", "start_page": 1, "end_page": 4, "date_of_service": "2025-12-29",
     "record_type": "permanent_stationary_report", "accepted_types": ["permanent_stationary_report", "progress_report"],
     "note": "PR-4, bundle starts at fax page 2/6, fax page 6/6 missing"},
    {"id": "r22", "start_page": 92, "end_page": 148, "date_of_service": "2024-10-02",
     "record_type": "hospital_record", "accepted_types": ["hospital_record", "operative_report"],
     "allow_internal_splits": true, "note": "admission chart printout, one record, splitting into notes is acceptable"},
    {"id": "r26", "start_page": 155, "end_page": 156, "date_of_service": "2024-10-02",
     "record_type": "consultation", "duplicate_of": "r25", "duplicate_kind": "near"}
  ]
}
```

Metrics, computed inside `labeled_pages` only:

- Start-page precision, recall, F1 (set of predicted record start pages vs gold start pages). A predicted start inside a gold record with `allow_internal_splits` is neither a hit nor a false positive; drop it from both sets.
- Exact span match rate (both start and end equal). For `allow_internal_splits` records, a set of predicted spans that exactly tiles the gold span counts as a match.
- Page assignment accuracy: for each page, the predicted span equals the gold span that contains the page (or lies inside it, for `allow_internal_splits` records).
- Date accuracy on matched spans.
- Type accuracy on matched spans: a hit when the predicted type is in `accepted_types` (or equals `record_type` when the list is absent).
- Duplicate detection: for each gold record with `duplicate_of`, a hit when the pipeline put the two records in one duplicate group. Report hits over gold duplicate pairs, and the count of predicted duplicate pairs that are not in the gold (false pairs).

Summaries, with `--judge`: for each record, one text call with `app/prompts/summary_judge.md`: given the transcription and the summary, list each factual claim in the summary and label it `supported`, `unsupported` or `unclear`. Report the supported rate and print the unsupported claims. Also a rule check without the model: every date string in the summary must appear in the transcription (normalized to YYYY-MM-DD, M/D/YYYY and Month D, YYYY forms).

Gold file source: a human labels spans by looking at the page images. `scripts/export_gold.py --doc-id <id>` dumps the current predicted spans as a gold file so the human only corrects the wrong ones. Reviewer edits in the UI are the second source. Derek is producing `eval/gold/record_1.json` in parallel; if it is not there, label the first 60 pages yourself from the rendered images and set `labeled_pages` to `[1, 60]`.

Print a short table and write `eval/results/<doc-id>.json`. Put the numbers in the README.

## 13. Build order and time boxes (start about 13:20 PT)

- M0, 10 min: repo skeleton, `requirements.txt`, `.env.example`, `.gitignore` (`data/`, `samples/`, `.env`, `.venv/`), `app/llm.py` with a probe script `scripts/probe_model.py` that sends page 1 of the sample as an image and asks for JSON. Confirm the model accepts images and returns JSON. Commit.
- M1, 50 min: schema, render, extract, group, summarize as `scripts/process.py samples/record_1.pdf`. First run with `MRT_MAX_PAGES=12`. Print the spans with boundary reasons, titles and dates. Look at the rendered pages and check the result by eye. Then start the full run in the background. Write `tests/test_group.py`. Commit.
- M2, 45 min: FastAPI, worker, upload, progress polling, timeline, detail panel. Commit.
- M3, 25 min: overrides and editing, retry and startup resume, `MRT_FAIL_AT_PAGE`. Commit.
- M4, 30 min: dedupe with tests, eval script, gold file, run eval, numbers into README. Commit.
- M5, 20 min: README, clean checkout test (`git clone` into a temp dir, create venv, install, run, drop the sample), warm `data/` with the sample bundles for the demo, write a 10-line demo script into the README. Freeze at 16:30.

If M1 slips past 14:30, cut M4's judge and the sort toggle first, then dedupe near matching (keep exact), then the detail panel images (keep transcription).

## 14. Engineering rules

- Use `AsyncOpenAI`. Never block the event loop: rendering and hashing go through `asyncio.to_thread`.
- OpenAI calls: Chat Completions with `response_format={"type": "json_schema", "json_schema": {"name": ..., "schema": ..., "strict": true}}`. If the API rejects it, fall back to `{"type": "json_object"}` and validate with pydantic. Use `max_completion_tokens`, not `max_tokens`. Do not set `temperature`. Image part: `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,...", "detail": "high"}}`. If Chat Completions rejects image input for this model, switch `llm.py` to the Responses API. Keep every model call inside `app/llm.py` so a change is one file.
- Log every model call: doc id, page or record, latency, prompt and completion tokens, attempt. One line each.
- One page failure never kills a document run. One document failure never kills the worker.
- SQLite: one connection per thread, WAL mode, `busy_timeout=5000`, short transactions. Wrap writes in a `threading.Lock` if you share a connection.
- Pydantic models for every JSON boundary. Reject and retry a model response that fails validation (count it as an attempt).
- Develop with `MRT_MAX_PAGES=12`. Run the full 188 pages at most twice: once after M1, once before the demo.
- Commit at the end of every milestone with a message that names the milestone.
- Keep README current as you go. Write the "Assumptions" section first and append to it.
- No gold plating: no auth, no React, no Docker, no migrations framework, no ORM.

## Appendix A: `app/prompts/page_extract.md`

System:

```
You transcribe and classify single pages from scanned medical record bundles for a workers' compensation case review. The data is private and processed locally for a licensed reviewer. Transcribe exactly what is on the page. Do not refuse, do not add commentary. Output JSON only, matching the provided schema.
```

User (text part, followed by the page image):

```
This is page {page_no} of {page_count} of the bundle "{filename}". Extract:

- transcription: the full text of the page in reading order. Keep headings, form labels with their values, dates, provider names and credentials, medication names, measurements. Write [REDACTED] for black redaction boxes and [ILLEGIBLE] for text you cannot read. Do not summarize. Do not invent text.
- fax_header: the fax transmission header or stamp lines at the top or side edge of the page, verbatim, joined with " | ". null if none. Examples: "10/30/2025 3:17:27 PM PDT PAGE 2/005 Fax Server | Received by TPA ICS Fax 10/30/25", "Dec/29/2025 2:01:57 PM Kaiser 2/6".
- fax_page: {"k": page number, "n": total} from the fax header, for example "PAGE 2/005" gives {"k": 2, "n": 5} and "2/6" gives {"k": 2, "n": 6}. null if none.
- page_label: the page numbering printed by the document itself, for example a footer "Page 2 of 5" gives {"k": 2, "n": 5}, "Page 3" gives {"k": 3, "n": null}. null if none. Do not confuse it with the fax header.
- starts_new_record: true if this page is the first page of a distinct medical record: a letterhead or form title at the top followed by patient identification, a report heading, a cover sheet, page 1 of a form, or a Kaiser chart printout whose first note starts here. false if the page continues a previous page: it starts mid-sentence or mid-list, the first section title says "(continued)", the page label is greater than 1, or the fax page k is greater than 1 with no new heading. A repeated patient header line (MRN, DOB) at the top of a chart printout page is not a new record. A fax page 2 that carries the full title and patient block of a form is a first page (fax cover pages were removed).
- boundary_confidence: "high", "medium" or "low" for that judgment.
- doc_type: one of progress_report (DWC PR-2), permanent_stationary_report (PR-4), office_visit, consultation, operative_report, hospital_record (admission, discharge, implant log, nursing), imaging_report, imaging_order, physical_therapy, lab_result, medication, work_status, correspondence, cover_sheet, billing, form_other, other.
- date_of_service: the date of the visit, exam, procedure, imaging or therapy session that this page documents, as YYYY-MM-DD. Not the fax date, not the date of birth, not the date of injury, not the print date. null if the page shows no service date.
- other_dates: every other date visible, as [{"date": "YYYY-MM-DD", "label": "fax date" | "report date" | "date of injury" | "dob" | "print date" | "other"}].
- provider: the treating or signing clinician as printed, with credential. null if none.
- facility: the organization, for example "Kaiser Permanente Occupational Health". null if none.
- patient_name: as printed, or null if redacted.
- gist: one sentence that says what this page is.
- is_blank_or_cover: true for blank pages, fax cover sheets and separator sheets.
```

JSON schema: generate it from the `PageSignals` pydantic model (`PageSignals.model_json_schema()`), with `additionalProperties: false` on every object for strict mode.

## Appendix B: `app/prompts/record_summary.md`

System:

```
You summarize one medical record from a workers' compensation file for a case reviewer. Use only the transcription provided. Do not add facts, do not guess values that are not on the pages. Output JSON only, matching the provided schema.
```

User:

```
Record from "{filename}", pages {start_page} to {end_page}. Page-level hints: doc types {doc_types}, service dates seen {dates}, providers seen {providers}.{partial_note}

Return:
- title: at most 80 characters, in the form "<document kind>: <subject>", for example "PR-2 progress report: left ankle fracture follow-up" or "Operative report: left ankle ORIF".
- record_type: one of the listed types.
- date_of_service: YYYY-MM-DD, the date of the encounter, procedure, imaging or therapy session. If the pages show only a report date or fax date, use it and set date_confidence to "approximate". If no date, null and "unknown".
- date_confidence: "exact", "approximate" or "unknown".
- provider, facility: as printed, or null.
- summary: 3 to 6 plain sentences for a reviewer who reads the whole timeline: reason for the encounter, key findings, treatment or plan, and work status if stated. High level, no transcription of forms, no new facts.
- key_findings: up to 5 short items, each a fact that is on the pages (diagnoses, procedures, imaging findings, restrictions, dates of future appointments).
- flags: from "partial record", "illegible sections", "multiple dates", "multiple patients", or empty.

Transcription:
{transcription}
```

`partial_note` = " This record starts mid-document (the bundle begins at fax page {k} of {n}); say so in flags." when `partial_start` is set, else empty.

## Appendix C: `app/prompts/summary_judge.md`

```
You check a summary against the source transcription of a medical record. List every factual claim in the summary as a short string. Label each claim "supported" if the transcription states it, "unsupported" if the transcription contradicts it or does not contain it, or "unclear" if the transcription is illegible or ambiguous at that point. Output JSON: {"claims": [{"claim": str, "label": str, "evidence": str}]}.

Summary:
{summary}

Transcription:
{transcription}
```

## Appendix D: README outline

1. What it does (3 sentences) and a screenshot or a short description of the screen.
2. Quick start: `python -m venv .venv`, activate, `pip install -r requirements.txt`, `cp .env.example .env` and set the key, `python -m app`, open `http://127.0.0.1:8000`, drop PDFs from `samples/`.
3. How it works: the four stages, the page as the unit of work, the grouping rules in 6 lines, dedupe in 3 lines.
4. Bonus features and their status: editing, retry (with the `MRT_FAIL_AT_PAGE` demo), duplicates, eval (with the numbers from `eval/results`).
5. Design decisions: vision per page instead of OCR; code decides boundaries, the model gives signals; extraction separated from summarization; deterministic record ids; SQLite checkpoints.
6. Assumptions: `gpt-6-luna` accepts images; timeline date is the date of service; a bundle can start mid-record; all PDFs belong to one patient.
7. What was cut and what comes next.
8. Demo script: 10 numbered steps.
