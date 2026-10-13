# Adopted evaluation labels

`record_1.json` in this directory is the single canonical gold file. Derek adopted
this reference on 2026-10-13 at 13:58 PT; see [DECISIONS-derek.md](DECISIONS-derek.md).
It contains 31 records over 188 pages, including one record spanning pages 37–41.
The original JSON is preserved byte for byte. Its embedded approval-pending status
predates Derek's adoption; the decision log records the later human approval.

`build_claude/eval/run_eval.py` resolves this file by default, independently of the
working directory. From `build_claude`, run:

```sh
.venv/bin/python -m eval.run_eval --filename record_1.pdf
```

The Claude drafts and old application-local snapshots are preserved under
[`docs/archive/`](../../docs/archive/README.md) at their original relative paths.
They are historical comparison evidence and are never selected by the default
evaluator. Use `--gold` explicitly when evaluating a separate draft.
`scripts/export_gold.py` exports predictions for review; its output does not replace
this adopted file or become canonical automatically.
