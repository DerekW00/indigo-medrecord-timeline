# Integration evidence — October 13

Status: in progress on `consolidate/demo`; no release approval is implied.

## Provenance and gates

- Runtime baseline: `5048b89f412b8c76fd8d91819469f7fdc8a0d7ac`.
- Task1 pushed launch SHA: `7269f0a5ef092b59ae04fee0594d0227c2e3265c`.
- Source document SHA: `d820c27277834856d2c017c4a3ff40f42a67cf1d6e70fcf088d65691a1cf1c50`.
- Baseline eval SHA256: `d454bc41f73a6be645d8e9b39e948e9a1197c4cc74e288d2f46c2822e68b588f` (from the frozen task contract; final preservation check pending).
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
