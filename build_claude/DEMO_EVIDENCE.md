# Integration evidence — October 13

Status: in progress on `consolidate/demo`; no release approval is implied.

## Provenance and gates

- Runtime baseline: `5048b89f412b8c76fd8d91819469f7fdc8a0d7ac`.
- Task1 pushed launch SHA: `7269f0a5ef092b59ae04fee0594d0227c2e3265c`.
- Source document SHA: `d820c27277834856d2c017c4a3ff40f42a67cf1d6e70fcf088d65691a1cf1c50`.
- Baseline eval SHA256: `d454bc41f73a6be645d8e9b39e948e9a1197c4cc74e288d2f46c2822e68b588f` (verified at 22:06 UTC; final preservation recheck pending).
- Source `/Users/dw/indigo/data/full188` stays immutable. Gold and evaluator remain unchanged.
- All Task5 runtime copies, logs, and medical artifacts stay under ignored `build_claude/data/task5`.
- Integration starts at 22:20 UTC (15:20 PT); target rehearsal 23:10 UTC; freeze 23:20 UTC.

## Commands and results

From `build_claude`, on the launch SHA:

```bash
MRT_DATA_DIR=/Users/dw/indigo-integration/build_claude/data/task5 MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q --basetemp=/Users/dw/indigo-integration/build_claude/data/task5/pytest-baseline
```

Result: **66 passed in 0.25s**. The first invocation had seven temporary-directory setup errors because the parent directory was absent (59 passed); creating the correct parent fixed the invocation without changing application code.

Pending: rolling lane merge checks; adversarial review hash/P1 disposition; accepted data backup and hashes; final offline suite; unchanged evaluator with `--judge`; committed clean-checkout smoke; integrated full188 UI/retry repetition; Mac tunnel walkthrough.

Only aggregate metrics belong in this tracked file. Raw judge claims, transcriptions, PDFs, images, database contents, and secrets remain local and ignored.

## Initial committed clean-checkout gate

```bash
.venv/bin/python -m scripts.clean_checkout_smoke --repo /Users/dw/indigo-integration --commit 2a96998ed25f6fe45d81b70cfab4e99e46956170
```

Result: **PASS, exit 0**. The clone installed its own 37 dependencies with `uv`, used its own Python 3.12 environment, generated synthetic PDFs, and ran without a key. Checks passed: two uploads complete; repeated identical upload reused; timeline and source JPEGs accessible; edit/restart/persist/reset; same-process retry from four saved pages to 12 done; completed pages and summaries unchanged on another retry. Log: ignored `data/task5/clean-checkout-2a96998ed25f.log`. This is a preparation check; it will be repeated after integration.

## Baseline preservation snapshot

At 22:06 UTC, source DB SHA256 was `4844ff9e1509939237108b913c0dc592776b1b78b4984be0d162117a103ba893`; gold SHA256 was `feed4fa2498a0146db63635665ee9100e74f29e47a916aaaec42b5719294cba9`. The saved PDF hash equals the document ID above; 188 rendered JPEGs exist. Only hashes/counts were emitted; no source text was added to Git. Snapshot: ignored `data/task5/baseline-provenance.json`.

## Lane checkpoints inspected before integration

- Task2 `e9b104eceb3393a116835f64e263016b072a27cd`: source repair completed, but S exits 1 because dates are 30/31. Boundaries are 31/31 matched gold spans, 188/188 assigned pages, 35 predictions, zero false starts; types 31/31; date-rule violations 0 across 35 summaries. Exactly two authorized vision page units and seven summary units ran (all attempt 1). There is no authorization for more extraction or gold-derived substitution. This date gate remains a release failure. Data handoff is idle at `/Users/dw/indigo-source-fixes/build_claude/data/source188`. These are owner-reported results pending integrated verification.
- Task3 F1 `9b9a4d9d58543f7ee3a8445e554cdf249f8281d2`: owner measured 1/2 duplicate pairs, zero false pairs, 29 focused tests. Its legacy near path remains WIP; final F2 checkpoint is required.
- Task4 `26c5bd67dbcce7af9bd9468b8b1c5af0a3a043d4`: owner reports six passing U/recovery tests. Mac tunnel walkthrough is explicitly unverified. Requested an optional test scratch root so integrated tests remain within Task5's runtime scope.

## P1: duplicate membership after reviewer edits

Task3 reported that PATCH/Reset never called the existing duplicate recomputation wrapper. Task5 reproduced the consequence with a synthetic API regression: hiding a canonical record left the visible timeline empty although its other source remained stored (**1 failed, 1 passed before fix**). Under the authorized unowned-core P1 exception, Task5 registered `app/api.py` and new `tests/test_api_integration.py` in its extra-owned list. PATCH and Reset now await the existing wrapper only when date/type/hidden overrides change; prose-only changes avoid recomputation. No schema or endpoint changes.

```bash
MRT_DATA_DIR=/Users/dw/indigo-integration/build_claude/data/task5 MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q --basetemp=/Users/dw/indigo-integration/build_claude/data/task5/pytest-api-fixed
```

Result after fix, before lane integration: **68 passed in 0.55s**; `git diff --check` passed. The external adversarial report was not yet present.
