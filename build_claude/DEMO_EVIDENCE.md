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

## Rolling integration and judge

Rolling merges began at **22:20:08 UTC**. Task2 merged as `03b2723` (96 tests passed); Task3 as `7fea981` (127 passed); Task4 as `67bc84e1a018b3aedff978b75f5be63e131c03c8` (133 passed in 26.68s, including full188 browser and recovery). All merged diffs were inspected and `git diff --check` passed.

J ran once on `67bc84e`, with all 35 completed summaries and unchanged evaluator/judge prompt. Actual command from `build_claude` (logging wrapper changes no evaluation behavior):

```bash
MRT_DATA_DIR="$PWD/data/task5/release188" OPENAI_MODEL=gpt-6-luna MRT_FAKE_LLM=0 MRT_CONCURRENCY=16 .venv/bin/python -c 'import logging, runpy; logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"); runpy.run_path("eval/run_eval.py", run_name="__main__")' --doc-id d820c2727783 --gold /Users/dw/indigo/eval/gold/record_1.json --judge --out "$PWD/data/task5/post-fixes-eval.json" > "$PWD/data/task5/judge-1.log" 2>&1
```

**J PASS:** 496/499 claims supported (0.994), 2 unsupported, 1 unclear, 0 call errors; 35 logical calls, each attempt 1. Report SHA256 `12f8221f5339c823ed0d0014e017103b1d3eee8243da72572bd11c69dee15476`. Raw claims/log remain ignored. The two unsupported claims are in spans 28–31 and 185–188. The judge sees capped transcription and key findings, not every source image.

## Latest user steering: F2 fallback and accepted F4 limitation

The coordinator delivered explicit Task5 ownership and verified Task3 was stopped at 22:22:35 UTC. Task5 reviewed the interrupted partial patch and completed scoped tests. Valid complete uncertain decisions qualify only for equal known service dates, equal types and clinical token-set Jaccard >= 0.85, stored as `similar`. Existing high-confidence confirmation remains. Distinct/different/complementary veto; invalid, failed, skipped, fake and incomplete responses cannot activate the fallback.

Focused suite: **79 passed in 0.68s**, including inclusive threshold, veto, invalid/error/missing-response, date/type gates, production path, and API edit/reset behavior.

The new rule was applied to the **existing real uncertain decision**, with source records, page text, complete transcriptions, model, prompt hash and score checked against its original Task3 store. No model reroll was performed. Local command:

```bash
.venv/bin/python data/task5/apply_saved_confirmation.py > data/task5/fallback-eval.log 2>&1
```

The local orchestration calls the existing safe-copy helper, validates/replays the saved decision through the production deduper, and invokes the unchanged evaluator. Output store: `data/task5/release-fallback188`; provenance: `fallback-provenance.json`; updated report: `eval.json`. **Duplicates 2/2, false pairs 0; both negative pairs remain separate.** One validated real decision reused; zero additional model calls. All source and summary fields are identical to the judged data, so the coordinator explicitly authorized preserving J. Boundary results remain 31/31 matched spans, 188/188 pages; types 31/31; model dates 30/31; date rule 0/35.

F4 is now a **user-accepted known limitation**, not a claimed 31/31 result. The requested r24 service-date edit to `2024-10-02`, refresh, server restart, persisted override and Reset passed in a disposable copy (**1 browser test passed in 5.50s**, no extraction/summary calls). Gold and model date remain unchanged.

A first U repeat on the two-group dataset exposed an outdated test assumption: it expected card count to stay fixed after date/type edits even though those edits now recompute membership. In fake mode no fresh near confirmation exists, so extra sources correctly become visible. The test now compares post-edit cards to the current API while still verifying both original real groups and all source links before editing. This was a test expectation failure (1 failed in 38.17s); the final full rerun is recorded below.
