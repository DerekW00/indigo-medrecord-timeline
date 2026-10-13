# Final integration evidence — October 13

Application candidate: **b36d945c67aa9473df212d0fbf0816fe24a3ec18** on `consolidate/demo`. This includes the completed UI polish, confirmed PDF removal, and final documentation cleanup. The evaluated pipeline remains the one verified at `47750cc`; extraction, grouping, summaries, and adopted gold are unchanged by the cleanup and removal feature. Mac forwarding cannot be independently verified by this Linux session.

## Verified release checks

| Check | Result | Local evidence |
|---|---|---|
| Full offline and browser suite | **216 passed in 53.14s** | `data/final/delivery-tests.log` |
| Clean checkout including PDF removal | **PASS**, fresh clone and `uv` environment at `b36d945` | `data/final/clean-checkout-b36d945c67aa.log` |
| PDF removal | Confirmation/cancel, processing guard, file/record/edit cleanup, duplicate promotion, preserved other PDFs, restart persistence, concurrent reupload, cleanup-failure retry | Full suite and clean-checkout logs above |
| Cached grouping replay | 188 validated page signals reproduce all 35 saved spans | `data/final/preservation-final.json` |
| Final evaluation with fresh judge | 31/31 matched gold spans; 188/188 pages; 30/31 dates; 31/31 types; 2/2 duplicates, zero false; 468/472 supported claims | `data/final/final-eval-raw.json`; committed aggregate at `../eval/results/final-eval.json` |
| Real two-PDF browser upload | 2 documents, 43 source records; eight cross-bundle copies; date interleaving with Show duplicates | `data/final/browser-results-candidate.json` |
| r24 edit/restart/reset | Saved 2024-10-02 survives restart; Reset restores actual model date 2024-11-12; all 35 original model rows unchanged | same browser report |
| Page-6 retry | Five saved pages preserved; page 6 fails once; Retry completes 12 without changing pages 1–5 | `data/final/retry6-before.json`, `retry6-after.json` |
| Persistent duplicate decision | Original validated uncertain result matches current text/date/type/model/prompt; replay makes zero model calls | `data/final/cache-release.log`, `cache-demo.log`, `cache-ready.log` |
| Final browser after cache | **PASS**: 43 records, 33 visible cards, ten stable groups; edit/restart/reset and visible 88.1% score; zero model calls | `data/final/final-browser-results.json` |
| Mac SSH tunnel | **Not verified by this Linux session** | Operator rehearsal required |

The full suite used the actual application and Chrome, with disposable copies of real source records and synthetic model-call spies. Its command, from `build_claude`:

```bash
MRT_DATA_DIR="$PWD/data/final/delivery-test-root" MRT_TEST_SCRATCH_ROOT="$PWD/data/final" \
  MRT_UI_DATA_SOURCE="$PWD/data/final/release188" MRT_UI_EXPECT_DUPLICATE_GROUPS=2 \
  MRT_TEST_BROWSER=/usr/bin/google-chrome MRT_FAKE_LLM=1 \
  .venv/bin/python -m pytest -q tests --basetemp="$PWD/data/final/pytest-final-delivery"
```

The earlier unqualified `pytest -q` collected old scratch clones beneath ignored `data/` and failed with duplicate module names. Selecting the complete `tests/` directory avoids those unrelated checkouts; no application defect was involved. The initial smoke assertion also compared mutable regrouping timestamps; it was corrected before this takeover to compare actual summary fields, with separate model-call spies proving reuse.

## Evaluation provenance

- Original PDF/document SHA256: `d820c27277834856d2c017c4a3ff40f42a67cf1d6e70fcf088d65691a1cf1c50`.
- Canonical root gold SHA256: `feed4fa2498a0146db63635665ee9100e74f29e47a916aaaec42b5719294cba9`.
- Immutable baseline DB SHA256: `4844ff9e1509939237108b913c0dc592776b1b78b4984be0d162117a103ba893`.
- Baseline report SHA256: `d454bc41f73a6be645d8e9b39e948e9a1197c4cc74e288d2f46c2822e68b588f`.
- Fresh final raw report SHA256: `258cadd3ab59b5cf5050df73424c7b2da6bfc8b9b57db777ee255bf25b58f5ae`.
- Final judge run: `2026-10-13T22:32:27+00:00`, real `gpt-6-luna`, all 35 completed summaries, 35 logical calls, zero call errors. No extraction was run.
- Judge: **468/472 supported; 2 unsupported, 2 unclear**. Unsupported spans: 157–165 and 185–188. The prior post-fix judge was 496/499; the README reports the latest run.
- Span score includes 30 exact gold spans and one permitted tiling. Four internal starts are ignored as allowed by gold. These are 35 predictions, not 31 literal predicted spans.
- The only date miss is r24: gold 2024-10-02, current model 2024-11-12. No gold-derived value was substituted into the model record.

Final evaluation used an isolated SQLite backup of accepted cached source data, checked the original valid uncertain response against byte-identical text, applied F2, then ran the existing evaluator with `--judge` against the adopted root gold. `data/final/replay_and_judge.py` records the local orchestration. All source records, page signals, and summaries remained unchanged. The only evaluator change selects canonical gold by default; scoring and judge prompt are unchanged.

Aggregate reports are committed. Raw clinical claims, PDFs, images, databases, detailed confirmation responses, and credentials remain local and ignored.

## Integration and the replay fix

Source fixes, dedupe, and UI were already integrated at `67bc84e` in that order, with recorded suite results of 96, 127, and 133 passing tests. The later task-3 tip `b1fd12e` is an empty checkpoint. The parked foundation rewrite was excluded.

This final pass raised the near candidate threshold from 0.80 to the requested **0.85**, fixed default gold lookup, exposed each duplicate member's real score, and documented F4 as the accepted editing example. The known near pair scores 0.880530973. Explicit different/distinct/complementary decisions remain vetoes; invalid, missing, failed, and fake confirmations never activate the fallback.

A real second-PDF upload exposed a reproducibility issue: dedupe asked the model again for unchanged original records, temporarily losing the near pair. The first reroll's detailed response was not captured; its failed grouping result was. A subsequent captured response was uncertain and restored the pair. The fix persists the **first validated decision** for identical source evidence, effective dates/types, model, and prompt. It caches vetoes too, invalidates on changed inputs, bypasses fake mode, and treats corrupt cache files as misses.

The existing original real uncertain decision was persisted only after matching its original source text, page ranges, dates/types, prompt hash, score, and model. Its source artifact SHA256 is `df8f0edeaa480b9b7714c71c23542b3592fdc01af3cd4e8c25389dd7d4e163c6`. The production deduper then verified two original groups and ten groups in the complete two-PDF store with all model calls forbidden. Neither the PT negative pair nor the complementary operative pair was grouped. This is documented cached replay, not a fresh model assertion.

## Final cleanup and demo state

The clean runtime checkout is `/Users/dw/indigo-demo`. Its store is `/Users/dw/indigo-integration/build_claude/data/final/mac-upload-ready`: the full sample plus rehearsal uploads `record_1_shuffled.pdf` (72 pages) and `record_1_tail.pdf` (40 pages) are done, with 61 total records. These extra uploads are separate from the published original-sample evaluation. The 36-page part PDF's validated extraction is cached but its document is not registered in this store. The browser-tested completed two-PDF store is `data/final/two-pdf-demo`. The isolated retry store `data/final/retry6-ready` is fresh, synthetic, fail-at-6, concurrency 1, on port 8006.

Cleanup moved old launch scripts, build plans, prompts, duplicate dependency/configuration examples, and draft labels into `docs/archive/`, preserving their contents. Current setup is in the root and application READMEs. The adopted gold remains at repository-root `eval/gold/record_1.json` with its original hash. Dirty historical worktrees and live uploaded PDFs were preserved; a local SQLite backup was taken before the demo update. The extra verification server on port 8010 was stopped.

The final UI uses calendar-safe dates, three-line summary previews with full detail text, corrected progress/count labels, and accessible copies buttons. Explicit filter changes cancel older pending copies navigation. PDF removal is available only after completion or failure; upload registration, retry, and removal are serialized so a concurrent reupload cannot lose its new files. Filesystem cleanup failure retains metadata for another removal attempt. Shared cached-page symlinks are removed without deleting their source targets. Removal was exercised only on synthetic test PDFs during finalization.

The part PDF is original pages 5–40. Preparation verified every mapped page's rendered pixels and cached JPEG hash. No summaries were copied into its initial store because the final source record is clipped. The browser check made eight new part-summary calls and three duplicate confirmations before persistence landed; zero vision calls and zero full-sample summary calls. Original model rows were verified unchanged. All later cache verification forbids model calls.

Known limits are in README: one model date miss, two unsupported judge claims, failed-page delay before grouping, capped judge input, one-patient assumption, and edits tied to unchanged boundaries. Finalization made no new extraction or judge calls. Exact operator commands are in [DEMO.md](DEMO.md). Freeze is 16:20 PT and demo is 16:30 PT.
