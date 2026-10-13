# Medical Record Timeline: parallel build and verification — Codex

Status: Codex proposal for later cross-AI comparison, prepared 2026-10-13. Application implementation has not started in this session. This proposal uses the architecture in [CLAUDE.md](CLAUDE.md) as an input; it has not been selected as a shared final plan. Proposed corrections are in [DESIGN_REVIEW-Codex.md](DESIGN_REVIEW-Codex.md).

Provenance: the initial fixture inspection and general risk assessment preceded reading CLAUDE.md. The final coordination proposal was informed by it. Author: Codex. Keep the other AIs' proposals separate until the user requests comparison and reconciliation.

## 1. Scope and verified starting point

Build a local, single-user application that combines records from multiple PDF bundles into one chronological timeline. Each card represents one source record, with its title, date, summary, and original PDF page references. Historical events mentioned inside a report do not automatically become separate records.

The baseline uses FastAPI, SQLite, PyMuPDF, and a static HTML/JavaScript UI. All application model calls use `gpt-6-luna`. Codex development and review agents use `gpt-6-astra`; these are separate model policies.

Verified in this session:

- Repository: `/Users/dw/indigo`; initial commit `43560d894411570b97b0131f96e409fe7576e9f0`. Only `.gitignore` was tracked at inspection. `CLAUDE.md` appeared during the session and was preserved.
- Existing worktrees: branch `main` at `/Users/dw/indigo`, branch `ui` at `/Users/dw/Indigo-ui`.
- Sample: `samples/record_1.pdf`, 95,423,040 bytes, 188 pages, one JPEG image per page, no extractable text, no encryption, no bookmarks. Checked across all pages with a temporary `pypdf` environment.
- Visual sample: pages 1, 2, 3, 60, 120, and 188. Multi-page continuations, fax/form numbering, redactions, and competing date types are present. Six pages do not establish the full record count or all boundaries. No application OCR accuracy or throughput has been measured.
- VM is Linux, with Python 3.12.3, `uv`, and Node 22 available. The baseline does not need Node. System OCR/PDF command-line utilities were not available at inspection.
- The UI worktree's `samples` symlink targets `/Users/dw/Indigo/samples`, which does not exist. The actual directory uses lowercase `indigo`. Also, the current `samples/` ignore rule does not ignore the symlink itself.

OpenAI documents image input and structured outputs for [`gpt-6-luna`](https://developers.openai.com/api/docs/models/gpt-6-luna). Its [file-input guide](https://developers.openai.com/api/docs/guides/file-inputs) limits PDFs to under 50 MB each and 50 MB combined per request. The baseline's local page rendering avoids sending the 95 MB PDF as one request. API-key access and actual request success still need a live probe.

### Scope order

1. Core: multi-PDF upload, readable scan extraction, reasonable record boundaries, chronological timeline, source images, visible progress and failures.
2. Persisted page and record results from the beginning; prove retry behavior after the first working vertical slice.
3. Persisted editing, followed by conservative duplicate handling if time remains.
4. Small independent source-based evaluation throughout; optional automated model judging last.

Cut near-duplicate automation, extra filters, visual polish, and model judging before cutting source images or scan support. A transcription alone cannot verify a transcription error in this image-only fixture.

## 2. Worktree ownership and integration

Use one integration owner and at most three concurrent workers. Reuse the verification worker slot for adversarial review after integration. Do not launch five independent feature branches with no shared contract.

| Lane | Checkout / branch | Owned work | Agent effort |
| --- | --- | --- | --- |
| Coordinator | `/Users/dw/indigo` / `main` | Design decisions, `app/models.py`, `app/config.py`, `app/db.py`, `app/api.py`, `app/worker.py`, entrypoint, dependency files, README, integration | Ultra for decisions and integration; delegate bounded execution when useful |
| Pipeline | proposed `/Users/dw/indigo-pipeline` / `pipeline` | `app/llm.py`, `app/pipeline/`, `app/prompts/`, `scripts/process.py`, `scripts/probe_model.py`, focused pipeline unit tests | High for boundary/retry logic; medium for agreed implementation |
| UI | existing `/Users/dw/Indigo-ui` / `ui` | `static/`, UI behavior checks | Medium |
| Verification | proposed `/Users/dw/indigo-verify` / `verification` | `tests/integration/`, `eval/`, source-label provenance, acceptance evidence | High for failure investigation; low for named checks |
| Adversarial review | clean checkout of an exact integrated commit in the verification lane | Read-only review with reproduction evidence; no feature edits | Ultra |

Every spawned agent uses `model="gpt-6-astra"` and an explicit effort. Give it a fresh, self-contained brief, its checkout path, allowed files, contract revision, and acceptance checks. A worker cannot change another lane's contract or schema without the coordinator resolving it first.

### Foundation before parallel implementation

The coordinator first creates and commits a runnable skeleton and the minimal shared contract:

- Pydantic types for page signals, record summaries, statuses, document progress, timeline items, and edit payloads in `app/models.py`.
- The database schema and DAO signatures used by the pipeline and worker. Freeze these before two lanes depend on them.
- Explicit JSON examples for the routes in CLAUDE.md section 9, including an undated record, failed document, duplicate source list, and edited value. Resolve whether absent, empty, and null override values differ.
- One synthetic timeline fixture, free of patient content, that both UI and API checks consume.
- Locked/pinned tested dependencies, `.env.example`, basic startup, and importable pipeline interfaces.

All workers branch from that same committed foundation. An untracked `CLAUDE.md` or uncommitted contract is not available automatically in another worktree. Commit only intended files; review staged paths for medical data and keys.

The existing UI worktree may belong to another active session. Inspect its status and coordinate with that owner before merging the foundation into `ui`; do not reset or recreate it. New worktrees can then be created from the foundation using standard Git:

```bash
git -C /Users/dw/indigo worktree add -b pipeline /Users/dw/indigo-pipeline main
git -C /Users/dw/indigo worktree add -b verification /Users/dw/indigo-verify main
```

These are future setup commands, not actions already performed. If a branch or directory already exists, inspect it rather than overwriting it.

### Runtime isolation

| Lane | `MRT_PORT` | `MRT_DATA_DIR` |
| --- | --- | --- |
| Integration/demo | `8000` | `/Users/dw/indigo/data` |
| Pipeline | `8001` | `/Users/dw/indigo-pipeline/data` |
| UI | `8002` | `/Users/dw/Indigo-ui/data` |
| Verification | `8003` | `/Users/dw/indigo-verify/data` |

- Each checkout gets its own virtual environment, SQLite database, uploads, rendered pages, and test outputs. Do not symlink `data/` or copy a live SQLite database.
- The original `samples/` may be shared read-only. Correct the existing UI symlink's case before fixture-dependent work, after confirming it is still a symlink. Use an ignore rule that covers both a directory and a symlink, such as `/samples`, in the shared foundation.
- Each lane loads credentials without printing them. Never commit `.env` or use a shared `.env` to assign the same data directory and port to all lanes.
- The coordinator owns paid full-fixture runs. Other lanes use deterministic fake model responses and bounded real probes. Preserve successful extraction results across retries.
- Run exactly one application worker process per data directory. Do not use multiple Uvicorn workers against this in-process queue design.

### Integration cadence

1. Merge the foundation into each clean lane before implementation starts.
2. Integrate a thin real upload-to-timeline path early; a UI using synthetic data is not core completion.
3. Workers commit cohesive changes and return the commit SHA, changed paths, checks run, and unresolved risks. The coordinator inspects the diff before merging.
4. Run integration checks on the merged commit. A passing pipeline branch and passing UI branch do not prove their combination works.
5. Freeze an exact commit for independent verification and review. If fixes change it, rerun affected checks on the new commit and record that SHA.

Do not push or create a pull request merely to integrate local work. If later requested, use `gh pr create --draft`. Do not force-push.

## 3. Build schedule and acceptance gates

Use time relative to the confirmed build start. The invitation calls its clock times typical; CLAUDE.md's statement about a particular day and timezone is not independently confirmed here. Budget: 195 minutes.

| Time | Coordinator / pipeline | Parallel UI / verification | Exit condition |
| --- | --- | --- | --- |
| 0–15 min | Shared skeleton, contracts, one actual image + structured-output API probe | UI reads contract; verifier selects small source-label set | Model call succeeds; source text is readable; exact model and latency recorded |
| 15–60 min | Persist render/extract/group/summarize; bounded 12-page trial | UI upload/progress/cards/source panel against fixture; tests for grouping/date/error cases | CLI produces source-linked records; manual source check; full 188-page run starts |
| 60–105 min | Upload/API/worker integration; measure full-run progress | Replace UI fixture with API; cross-PDF smoke and source checks | Real PDF upload produces timeline in browser; failures visible |
| 105–140 min | Persisted edits; retry and restart proof | Verify edited sort order, repeated retry, interrupted extraction/summarization | Edits survive restart; completed work is reused |
| 140–165 min | Fix core defects; exact duplicate handling if core is sound | Independent small evaluation; adversarial review of integrated commit | No known demo-blocking defect; all unprocessed pages accounted for |
| 165–195 min | Freeze features; README; clean-checkout verification; rehearse | Recheck fixes and report exact evidence | Fresh checkout runs; demo ready; limitations written down |

### Early decisions and cuts

- At minute 15, measure readability and actual model latency. Do not infer all 188 pages will process in seconds. Estimate extraction time from the measured average and concurrency, allowing for retries and summary calls.
- At minute 60, if the small pipeline has not produced credible records, stop bonus work and fix extraction/boundaries. Keep UI work to core upload/progress/source viewing.
- At minute 105, if upload-to-timeline is not integrated, stop adding features. Work on one failing path together.
- At minute 165, stop adding features. Fix only blockers and prepare the deliverable.

### Required verification

These are planned checks; none has passed against an application yet.

- **Boundary tests:** one multi-page record; two records back to back; a repeated header; a changed fax header with continuing printed labels; two records within one fax; missing first page; blank/cover pages. A two-records-on-one-page case documents the whole-page segmentation limitation unless support is explicitly added.
- **Date tests:** encounter date versus fax/print date; an unknown date; a partial date; date edits that change sort order; deterministic ties. No invented date precision.
- **Two-file timeline:** source files are uploaded in reverse order and contain interleaving dates. Results sort globally and retain correct document/page references.
- **Retry test:** fail one page after other pages finish, retry twice, and restart during summarization. Compare persisted completed-unit IDs and fake-model call counts; successful work must not be repeated and the queue must not create duplicate logical runs.
- **Editing test:** save, refresh, restart, retry, and clear an optional field. Confirm the effective result and original generated result remain distinguishable.
- **Development-cap test:** process 12 pages, then request the full file in the same data directory. Completion cannot mean 12/188, and remaining pages must be eligible for processing.
- **Summary test:** inspect original images for selected records; compare date, title, findings, and plan. Include a long record with a key fact in its middle. Model judging of its own transcript does not validate OCR accuracy.
- **Duplicates:** exact repeated record retains all sources; distinct same-date/same-type records remain visible; uncertain similarity is not a reason to silently hide a card.
- **Full fixture:** report total, successful, failed, and unprocessed pages; account for all 188. Partial output must stay visibly partial.
- **Clean checkout:** test the frozen commit with a new environment and empty data directory using README instructions. Supply the ignored sample separately, or use a committed synthetic PDF fixture. Do not rely on another worktree's files, symlinks, database, or installed dependencies.

The verification lane should provide deterministic unit/integration commands under `python -m pytest`, plus a separate opt-in real-model smoke command. Network access and an API key must not be required for ordinary tests. Capture exact commands, exit codes, commit SHA, fixture scope, and untested cases.

### Independent labels and adversarial review

Prepare a small independently checked set of source spans while implementation proceeds. Do not turn uncorrected model predictions into ground truth. Record which original images were inspected and who verified the labels. Start with a manageable contiguous subset and a few difficult boundaries; do not assume someone has already labeled all 188 pages.

The adversarial reviewer receives the assignment, contracts, fixture scope, and exact integrated commit. Ask for reproducible failures rather than general style advice. Review source omission, wrong-date selection, record merges/splits, retry corruption, lost edits, false duplicate merges, and clean-checkout failures. Fix highest-impact issues first and state any remaining limitation honestly in the README and demo.

## 4. Demo and review decisions

Suggested opening for the intro:

> I will first prove extraction on the scanned sample, then build a page-aware pipeline with saved progress and a timeline linked to original pages. I will separate pipeline and UI work behind one agreed API contract, and independently test the integrated result. Once the core works, I will prioritize saved edits and recovery. I will cut automatic near-duplicate merging and extra UI features if time is tight.

Suggested demo sequence:

1. Explain the scan constraint and chosen record-boundary policy.
2. Upload a small live fixture; show processing and the combined sorted timeline.
3. Open one multi-page record and verify its summary against the original images.
4. Show the completed full sample, distinguishing cached results from live processing.
5. Demonstrate saved edits and recovery only if implemented and tested.
6. Show the evaluation scope, one known limitation, and the clean-checkout instructions.

Decisions for the later comparison and implementation handoff:

1. Select the architecture baseline after comparing the AIs' proposals. This Codex proposal uses Python plus a static UI.
2. Review the concrete corrections in DESIGN_REVIEW-Codex.md before workers depend on those behaviors.
3. Confirm the owner of the existing `ui` worktree before integration writes there.

Review now: check the authorship/provenance and the proposed lane ownership; flag any corrections. Architecture selection and acceptance of design changes can wait for the requested diversity review. Preparation alone does not imply authorization to begin the full build.
