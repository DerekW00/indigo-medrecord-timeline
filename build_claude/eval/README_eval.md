# Eval: are the boundaries and summaries right?

Two checks, both run from the project root with the venv Python.

## 1. Make a gold file (once per sample PDF)

```
python scripts/export_gold.py --doc-id <id or prefix>      # writes eval/gold/<stem>.json
```

It dumps the current predicted spans in the gold shape (`id`, `start_page`, `end_page`,
`date_of_service`, `record_type`, `accepted_types`, `note`). A human then looks at the page
images and corrects the wrong spans. Optional per-record keys: `allow_internal_splits: true`
(a chart printout that may be split into its notes), `duplicate_of: "r25"` with
`duplicate_kind: exact|near|partial`. `labeled_pages: [lo, hi]` limits scoring to the pages
that were actually labeled. Reviewer edits made in the UI are applied before the export.

## 2. Score a document

```
python eval/run_eval.py --doc-id <id or prefix> [--judge]  # writes eval/results/<doc-id>.json
```

Metrics, computed inside `labeled_pages` only (spans are clipped to that window):

- **start pages** precision, recall, F1 of predicted vs gold start pages. A predicted start
  strictly inside an `allow_internal_splits` record is ignored.
- **exact spans**: start and end both equal. An `allow_internal_splits` record also counts
  when the predicted spans tile it exactly.
- **page assign**: a page is right when its predicted span equals the gold span holding it,
  or lies inside it for an `allow_internal_splits` record.
- **dates, types** on matched spans. Type is a hit when it is in `accepted_types` (or equals
  `record_type`). For a tiled match the head piece (the span starting at the gold start) is
  compared. Dates compare after normalization, so `10/05/2024` equals `2024-10-05`.
- **duplicates**: gold pairs are the transitive closure of `duplicate_of`. A hit when the
  pipeline put both records in one duplicate group. `false pairs` counts predicted pairs
  the gold does not mark. `unmapped` means a boundary error made the pair impossible.
- **date rule** (no model, no gold needed): every date in `date_of_service`, `summary` and
  `key_findings` must appear somewhere in the record's transcription, in any written form.
  A numeric date needs the same separator on both sides (`/` or `-`), so pain scales and
  decimals such as `2.5/10` or `3.5-40` are not read as dates. A two-digit year is read as
  at most ten years ahead of today, so `1/5/49` is 1949.
- **judge** (`--judge`, one model call per record): each claim in the summary and key
  findings is labeled supported / unsupported / unclear; the supported rate is reported and
  unsupported claims are printed.

Known inflation: the first page of the window is a start in both sets, so one hit is free.

Pure metric functions live in `eval/metrics.py`; `tests/test_eval_metrics.py` covers them.
