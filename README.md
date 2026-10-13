# Medical Record Timeline

Review scanned medical-record PDF bundles as one chronological timeline, with source pages, saved reviewer edits, resumable processing, and duplicate detection.

The application lives in [`build_claude/`](build_claude/README.md). Start with its [uv quick start](build_claude/README.md#quick-start), [before/after evaluation](build_claude/README.md#features-and-final-evaluation), and [eight-step demo commands](build_claude/DEMO.md).

Final cached evaluation: **31/31 matched gold spans, 188/188 assigned pages, 30/31 dates, 31/31 types, 2/2 duplicate pairs with zero false pairs; 468/472 judge claims supported.** The known r24 date error is the persisted-edit demo. No new 188-page extraction was run.

[Canonical gold](eval/gold/record_1.json), [final aggregate report](eval/results/final-eval.json), and [verification evidence](build_claude/DEMO_EVIDENCE.md) are included. PDFs, page images, databases, raw clinical claims, and API keys remain outside Git. The runtime is local; model processing uses OpenAI `gpt-6-luna`.
