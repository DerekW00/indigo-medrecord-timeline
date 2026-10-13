# Medical Record Timeline

Review scanned medical-record PDF bundles as one chronological timeline, with source pages, saved reviewer edits, resumable processing, and duplicate detection.

## Run it

Requires Python 3.11+ and `uv`. From this repository:

```bash
cd build_claude
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp env.example .env
# Set OPENAI_API_KEY in .env.
.venv/bin/python -m app
```

Open http://127.0.0.1:8000 and drop PDFs. The screen provides a combined timeline, source-page viewer, persistent editor, progress and retry controls, and clickable duplicate badges. Data is stored locally; page images and record text are sent to OpenAI `gpt-6-luna` for processing.

## Release evidence

Final cached evaluation: **31/31 matched gold spans, 188/188 assigned pages, 30/31 dates, 31/31 types, 2/2 duplicate pairs with zero false pairs; 468/472 judge claims supported.** The known r24 date error is the persisted-edit demo. No new 188-page extraction was run.

[Application guide and known limitations](build_claude/README.md), [before/after evaluation](build_claude/README.md#features-and-final-evaluation), [eight-step demo commands](build_claude/DEMO.md), and [verification evidence](build_claude/DEMO_EVIDENCE.md) describe the final version.

The [canonical gold](eval/gold/record_1.json) and [final aggregate report](eval/results/final-eval.json) are included. PDFs, page images, databases, raw clinical claims, and API keys remain outside Git. Earlier build plans, launch scripts, and draft labels are preserved in [docs/archive](docs/archive/README.md).
