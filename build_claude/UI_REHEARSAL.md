# UI and retry rehearsal — Task 4

Status: local browser and recovery checks pass. Mac walkthrough is unverified.
Runtime: existing `build_claude/` app on branch `ui`. No API, worker, schema,
model adapter, or pipeline changes. All tests launch the real API and worker.
No demonstrated UI defect surfaced, so the existing static files are unchanged.
Baseline: `7269f0a5ef092b59ae04fee0594d0227c2e3265c`. First pushed checkpoint:
`26c5bd67dbcce7af9bd9468b8b1c5af0a3a043d4`; final SHA is in the external Task4
status/checkpoint file and `git log` (avoids a self-referential commit SHA).

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
MRT_UI_DATA_SOURCE=/Users/dw/indigo/data/full188 MRT_FAKE_LLM=1 \
  .venv/bin/python -m pytest -q tests/test_ui_browser.py tests/test_resume_demo.py
MRT_UI_DATA_SOURCE=/Users/dw/indigo/data/full188 MRT_TEST_RETRY_PORT=8006 \
  MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q
```

Results on October 13:

- Recovery alone: **3 passed in 7.51s**.
- U plus recovery using a disposable backup of ui188: **6 passed in 22.77s**.
- U plus recovery using a disposable backup of full188: **6 passed in 22.75s**.
- Full suite, including all five edited fields through restart/reset and browser
  Retry on port 8006: **72 passed in 24.24s**, zero skipped tests.
- Final full rerun after adding integration controls: **72 passed in 21.06s**,
  zero skipped tests, with `MRT_TEST_SCRATCH_ROOT="$PWD/data/task4-final"`,
  `MRT_UI_EXPECT_DUPLICATE_GROUPS=0`, the full188 source and retry port 8006.
- Focused full188 check with the explicit zero-group baseline assertion:
  **1 passed in 7.92s**. Dev requirements install succeeded (11 packages checked).
- **188/188 source image URLs** returned JPEGs; **36 baseline cards**; zero model
  calls on completed full188 data; no browser JavaScript errors in that flow.
- `git diff --check` passed. Only the four owned test/dependency/rehearsal files
  changed; no data, images, PDFs, secrets or raw medical output are staged.

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
source links and browser Retry. Uses installed `/usr/bin/google-chrome`
150.0.7871.181 on Linux with Playwright 1.63.0. Browser dependencies are dev-only.

The full188 test derives record/card counts from the real API, accepts the
repaired 35-record store, and checks every actual duplicate group's badge,
members, source navigation and collapse behavior. Baseline has zero groups;
real repaired duplicate results were not claimed here. Task5 should repeat U
with `MRT_UI_DATA_SOURCE` set to its accepted completed store and
`MRT_UI_EXPECT_DUPLICATE_GROUPS=2`; this fails if either expected group is absent.
Set `MRT_TEST_SCRATCH_ROOT="$PWD/data/task5"` to keep integration's disposable
copies under its own task scope; the default is `$PWD/data/task4-tests`.

## Persistent local review server

Started the existing app on `127.0.0.1:8000`, PID `193902`, serving only
`/Users/dw/indigo/data/ui188`. Local Chrome verified 36 cards, keyboard selection,
detail form and source pages on this exact port. The UI copy has zero reviewer
overrides. No CLI writer shares it. The baseline pipeline was never run.

PID/log files are ignored at `build_claude/data/task4-review/server.pid` and
`server.log`. Stop this owned process with `kill 193902` before starting a
replacement on port 8000. All disposable test servers, including port 8006,
were stopped automatically. No retry server is left running.

## Mac tunnel walkthrough — not performed

This session runs on Linux and has no remote Mac browser access. Local Chrome
checks do not establish Mac tunnel or operator walkthrough success.

On the VPS, start the UI copy with this branch's static files (port 8000 must be
free; the Task4 server above is already running; do not launch a second one or
run a CLI writer against this store):

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

The exact automated local retry equivalent is:

```bash
MRT_TEST_RETRY_PORT=8006 MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q \
  tests/test_ui_browser.py::test_browser_same_process_retry
```

Record operator/date/browser and each actual result before marking Mac verified.
Task5 owns integration, final repaired-data duplicate verification and release
acceptance. This document does not claim those downstream checks have passed.
Real-model summary quality, corrected service dates/boundaries, final duplicate
metrics, final judge and clean-checkout release checks remain outside Task4.
The external adversarial review had not arrived at the last pre-handoff check;
Task5 remains responsible for reading it and coordinating any ownership request.
