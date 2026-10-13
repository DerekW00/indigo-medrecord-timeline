# UI and retry rehearsal — Task 4

Status: work in progress. Recovery checks pass; browser checks are running.
Runtime: existing `build_claude/` app on branch `ui`. No API, worker, schema,
model adapter, or pipeline changes. All tests launch the real API and worker.

## Data and execution isolation

- The coordinator supplied `/Users/dw/indigo/data/ui188`, copied from immutable
  full188. Verified 188 done pages and rebased its image paths into the UI copy.
- Full188 browser tests make a consistent SQLite backup and copy images/uploads
  into a disposable store under `build_claude/data/task4-tests/`.
- Fake-only subprocess spies count calls without logging medical text. Full188
  checks require zero extraction/summary calls. Synthetic retry/upload tests use
  separate fresh stores. Helpers close their own servers and delete scratch data.
- No real model calls, new real extraction, gold changes, or medical artifacts
  are part of this lane's commits.

## Commands and current results

From `build_claude/`:

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q tests/test_resume_demo.py
MRT_UI_DATA_SOURCE=/Users/dw/indigo/data/ui188 MRT_FAKE_LLM=1 \
  .venv/bin/python -m pytest -q tests/test_ui_browser.py tests/test_resume_demo.py
```

Recovery: **3 passed in 7.51s**. Browser run: pending.

1. Synthetic 12-page PDF, concurrency 1, injected failure at page 5. Four saved
   pages remain done; page 5 fails; later pages remain pending. Same-process HTTP
   Retry reaches 12/12, pages 1–4 keep their attempt counts, and each successful
   page-model call occurs exactly once.
2. Summary failure/retry: two completed summaries retain one call each; only the
   failed summary repeats. No extraction repeats.
3. Abrupt process termination during extraction: restart automatically resumes;
   every previously saved page retains exactly one model call.

Browser coverage: all 188 image URLs, pages 151/154 images and transcription,
filters, duplicate toggle, saved edits through refresh/process restart/reset,
polling while typing, keyboard Enter/Escape, two synthetic uploads, duplicate
source links and browser Retry. Uses installed `/usr/bin/google-chrome` on Linux.

## Mac tunnel walkthrough — not performed

This session runs on Linux and has no remote Mac browser access. Local Chrome
checks do not establish Mac tunnel or operator walkthrough success.

On the VPS, start the UI copy with this branch's static files (port 8000 must be
free; do not run a CLI writer against this store):

```bash
cd /Users/dw/Indigo-ui/build_claude
MRT_DATA_DIR=/Users/dw/indigo/data/ui188 MRT_MAX_PAGES=0 MRT_PORT=8000 \
  .venv/bin/python -m app
```

On the Mac, keep the tunnel terminal open:

```bash
ssh -N -L8000:127.0.0.1:8000 vps
```

1. Open `http://127.0.0.1:8000`; verify the completed 188-page source and timeline.
2. Filter by type/source; open records containing pages 151 and 154, expand
   transcription and open the source image in a new tab.
3. Open a record and type a temporary title. Wait through two polls. Save,
   refresh, restart the VPS server, reopen the record, then Reset and confirm.
4. Toggle Show duplicates and follow another source from the detail panel.
   Baseline data has no duplicate groups; use Task3's accepted data for real
   duplicate badges during Task5 integration. Synthetic automation covers them.
5. Run retry on a separate fresh synthetic store at port 8006 with fake model,
   concurrency 1, fail-at-page 5; keep the same process alive for Retry. Never
   inject failure into the completed medical store.

Record operator/date/browser and each actual result before marking Mac verified.
Task5 owns integration, final repaired-data duplicate verification and release
acceptance. This document does not claim those downstream checks have passed.
