# Revised build plan — measured fixes and demo readiness

**AUTHORIZED:** Derek replied `go` with the guardrails below. Freeze **16:20 PT**; demo **16:30 PT**, October 13. Actual launch preparation began about **14:59 PT**.

**Latest execution guardrails (override older schedule/prompt wording):**
- All five task sessions run in tmux session `tasks`, one pane per task. Exact launch commands and logs live in `/Users/dw/indigo-task-control/`.
- Every active task commits and pushes its own branch at least every10min, including clearly labeled WIP checkpoints. No force pushes. Checkpoints are not automatically passing release candidates. Automatic checkpoints use only explicitly owned files and stop on Git conflicts.
- Task5 starts preparing immediately after the baseline barrier and **starts merging available pushed checkpoints at15:20 PT**, then continues incrementally; it must not wait for all lanes to finish. Final acceptance still requires the full checks.
- Separate `MRT_DATA_DIR` per task. UI copy is created with `cp -r data/full188 data/ui188` from `/Users/dw/indigo`; its server uses the resulting `/Users/dw/indigo/data/ui188`. Preserve the source store. Browser/retry tests use additional fresh task-local scratch stores.
- **No real re-extraction outside p151–152.** Cached pages are reused everywhere else; fake synthetic smoke calls are not real re-extraction.
- Expect `/Users/dw/indigo/DESIGN_REVIEW-adversarial.md` about10min after launch. Task5 reads it on arrival and applies its P1s. Other tasks may act early only if a P1 blocks their current task; record the reason. For a P1 in a lane-owned file, Task5 obtains a pushed checkpoint and explicit ownership handoff before editing that file; that lane then leaves it untouched. This bounded P1 exception supersedes earlier file-ownership restrictions without concurrent writers.
- Runtime remains the existing build. The parked broad foundation branch stays excluded. Root coordinator monitors starts, checkpoint/push timing, handoffs, adversarial arrival and freeze.

**Baseline branch is `claude-build` (`16e37fe`, `5048b89`): 66 tests pass, five stages, CLI/API flow and eval harness already exist. Start from it; do not rebuild those capabilities.** Latest instructions supersede the broad foundation and repair program in the earlier TASKS/FINAL_DESIGN proposal. `CLAUDE.md` remains the product contract, with the five explicitly requested changes below.

## Evidence and preserved work

| Evidence | Verified baseline / treatment |
|---|---|
| Committed code | `claude-build=5048b89f412b8c76fd8d91819469f7fdc8a0d7ac`, following `16e37fe`. An isolated checkout passed all 66 offline tests. Builder reports five-stage fake CLI/API upload, polling, timeline, PATCH and reset. |
| Run 1, 14:36 PT | Derek reports real `gpt-6-luna`, concurrency8, 12/12 pages in27s, about8–12s/page and3.5K prompt/1K completion tokens/page. Spans1–4,5–8,9–12 and dates2025-12-29,2025-10-29,2025-09-15 match gold. Preserve this run; no repeat needed. |
| Run 2 | Read-only inspection confirms `/Users/dw/indigo/data/full188/app.db`:188 done pages,36 done records, document done, zero reviewer overrides. Real model, concurrency16. Full document ID: `d820c27277834856d2c017c4a3ff40f42a67cf1d6e70fcf088d65691a1cf1c50`. |
| Saved eval | `/Users/dw/indigo/data/full188-eval.json` confirms the numbers below; SHA256 `d454bc41f73a6be645d8e9b39e948e9a1197c4cc74e288d2f46c2822e68b588f`. Its `run_at` is14:40:51 PT; Derek labels the completed baseline14:43. The reported `eval/results/baseline-14-43.json` copy was absent at inspection; use the existing source report. |
| Gold | `/Users/dw/indigo/eval/gold/record_1.json`,31 human-adopted spans; pp37–41 one record. Gold and source PDFs stay read-only. No pipeline imports gold or hard-codes these pages. |
| Uncommitted grouping patch | Live root has changes in `build_claude/app/pipeline/group.py` and `tests/test_group.py`. The measured run must not be attributed solely to committed code. Task2 receives/reviews this patch, preserves useful changes and records its eventual SHA before reproducing the result. Do not reset or blindly overwrite it. |
| Parked foundation | `/Users/dw/indigo-foundation`, branch `consolidate/foundation`, contains unfinished uncommitted shared-contract work. Its 32 focused tests pass; full suite was 97 passed/1 failed because retirement intentionally contradicts an old deletion test. **Do not merge or cherry-pick that broad diff.** New schema, retirement, mock API and adapter changes are outside this revision. Preserve the work for later. |
| Current action | This rescope only reads saved data/source and changes TASKS.md. No new model run, application patch, branch merge or task launch is authorized by this document. |

| Metric | Before, real full188 | Required after |
|---|---|---|
| Predicted records |36;31 gold | Expected35 with four permitted internal admission splits; do not force31 predictions. |
| Start pages |P0.969 / R1.000 / F10.984;31TP,1FP,0FN;4 internal starts ignored |P/R/F1=1.000;0 false starts, including no p154 start. |
| Matched gold spans |30/31:29 exact +1 permitted tiled |31/31:30 exact +1 permitted tiled, unless valid internal partitioning changes. This is not31 literal exact spans. |
| Page assignment |186/188=0.989 |188/188. |
| Service dates |29/30 matched spans=0.967 |30/30 on the original matched cohort; **31/31 overall after F3 adds r25**. |
| Types |30/30=1.000 |No regression;31/31 after F3. |
| Duplicates |0/2 gold pairs;0 false |F1 checkpoint1/2; final2/2;0 false at both gates. |
| Date rule |0 violations;36 summaries checked |0 violations; report actual post-fix summary count. |
| Judge |464/473 supported=0.981;6 unsupported,3 unclear,0 errors |Unsupported≤2;0 call errors; all completed summaries judged. Report fresh supported/total and unclear counts; do not retain 473 as the denominator. |

## Five tasks, ownership and schedule

All owned paths below are relative to `build_claude/` unless prefixed `root:`. Ownership is exclusive for the whole revision. Reading and merging another task's files is allowed; editing them is not. Standard branches with explicitly authorized checkpoint pushes; no deployment or force push.

| Task / merge order | Owner | Branch and worktree | Owned files | Minutes / window | Acceptance command and outcome |
|---|---|---|---|---|---|
| **1. Baseline handoff and interface freeze** |claude-main |`main`; `/Users/dw/indigo-release` |`root:TASKS.md` only; baseline merge, no runtime edits |5;14:50–14:55 |`git merge --ff-only 5048b89f412b8c76fd8d91819469f7fdc8a0d7ac`; then `MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q` from `build_claude`:66 pass on the pinned baseline; publish `BASE_SHA`. Run in release worktree, never switch/reset the live checkout. |
| **2. Measured source fixes: F3/F4/F5** |claude-A |`fix/measured-source`; `/Users/dw/indigo-source-fixes` |`app/pipeline/group.py`, `app/pipeline/summarize.py`, `app/prompts/page_extract.md`, `app/prompts/record_summary.md`, `tests/test_group.py`, new `tests/test_measured_source_fixes.py`, new `scripts/replay_measured_fixes.py` |30;14:55–15:25 |`MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q tests/test_group.py tests/test_measured_source_fixes.py`; then command **S** below:31/31 spans,188/188 pages,31/31 dates/types. F5 final judge gate **J** runs once after integration. |
| **3. Duplicate fixes: F1 then F2** |codex-review |`fix/measured-dedupe`; `/Users/dw/indigo-dedupe` |`app/pipeline/dedupe.py`, new `app/prompts/duplicate_confirm.md`, `tests/test_dedupe.py`, new `tests/test_measured_duplicates.py`, new `scripts/check_measured_duplicates.py` |40;14:55–15:35. F1 checkpoint by15:05; final F2 uses Task2 output after15:25 |`MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q tests/test_dedupe.py tests/test_measured_duplicates.py tests/test_eval_metrics.py`; commands **D1/D2**:1/2 then2/2 duplicates,0 false; PT and complementary records retained. |
| **4. Full188 UI and retry rehearsal** |codex-ui |existing `ui`; `/Users/dw/Indigo-ui` |`static/index.html`, `static/app.js`, `static/style.css`, new `requirements-dev.txt`, new `tests/test_ui_browser.py`, new `tests/test_resume_demo.py`, new `UI_REHEARSAL.md` |35;14:55–15:30 |`MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q tests/test_resume_demo.py`; **U** browser command plus Mac tunnel walkthrough below. Save/restart/reset, polling, sources and retry prove the current product works. |
| **5. Integrate, judge, README and clean checkout** |claude-main; codex-review checks final evidence |`consolidate/demo`; `/Users/dw/indigo-integration` |`root:README.md`, `README.md`, `env.example`, new `DEMO_EVIDENCE.md`, new `scripts/clean_checkout_smoke.py` |rolling from15:20; target16:10, freeze16:20 |Full `MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q`; **J** and **C** below; repeat UI/retry on the integrated SHA; record before/after table and10-step demo. |

**Preferred dependency order:**1 →2 →3 →4 →5; the latest guardrail requires Task5 to merge whatever pushed checkpoints exist at15:20 and continue incrementally. Tasks2/3/4 start concurrently from the exact `BASE_SHA` after Task1. Task3 implements/measures F1 first; its final F2 acceptance depends on Task2 fixing r25, even though F2 is higher in value order. Task3 merges Task2's passing commit without editing its files before final duplicate verification. Existing `ui` is merged forward from `BASE_SHA`; never recreate/reset it.

**Critical path from14:50:**5min baseline →40min longest parallel lane →35min integration =80min, finish16:10,10min reserve before 16:20. Task2 output at15:25 leaves 10 min for final F2 confirmation. UI/README preparation can proceed before final metrics. If `go` arrives later, shift the schedule and consume reserve explicitly; do not silently move freeze. By 15:50 all required feature fixes must be merged or recorded as uncompleted; the remaining 30 min is verification/rehearsal.

Review patches must be read and mapped to the owning task before launch. Apply only bounded, relevant fixes; do not turn incoming reviews into another rebuild. Each owner returns commit SHA, actual command output, metric movement and remaining risks. A failed check returns to that owner; integration does not edit their files.

### Frozen interfaces and data handling

- Reuse committed `app/models.py`, `db.py`, `config.py`, `llm.py`, `api.py`, `worker.py`, render/extract stages and eval harness. No schema expansion or model/DAO signature changes in these tasks. Existing deterministic file/span IDs, override columns and `date_confidence` remain the contract. [CLAUDE §§4–6,9,14]
- `group_pages(list[PageSignals]) -> list[RecordSpan]`; `group_document(doc_id) -> list[dict]`; `summarize_document(doc_id)` stays async and returns failed-record count. Existing `sync_records` preserves done summaries for unchanged spans: replay must deliberately invalidate affected summaries in its disposable copy. [§§6–7]
- Worker already runs either sync or async stages through `call_stage`; API already awaits `dedupe_all_async()`. Task3 may make `dedupe_all()` async and keep `dedupe_all_async()` as an awaitable compatibility wrapper; both publish the existing duplicate table shape. Update only its owned tests/call sites; no API/worker rewrite. [§§6,8–9]
- Duplicate row stays `{record_id,group_id,is_canonical,reason,score}`. New reasons: `partial_exact` and `similar_confirmed`; complete image copies may use `exact_image`. Existing API exposes group/copies/other sources. UI uses current paths: `GET /api/documents`, `/api/timeline?include_duplicates=1`, `/api/records/{id}`, `PATCH /api/records/{id}`, `DELETE /api/records/{id}/overrides`, `POST /api/documents/{id}/retry`, `/pages/{doc_id}/{page_no}.jpg`. No new endpoint required. [§§8–10]
- Confirmation response is a local Pydantic type in Task3's module: `{relation:'duplicate'|'complementary'|'distinct'|'uncertain',confidence:'high'|'medium'|'low',reason:str,a_pages:list[int],b_pages:list[int],unique_content:str}`. Use existing `llm.text_json`; fake mode returns an explicit uncertain decision locally, while tests inject confirmation outcomes. Never require a new fake adapter/schema. [§§8,14]
- Runtime remains Python/FastAPI/SQLite/PyMuPDF/vanilla JS, app model only `gpt-6-luna`. `uv venv --python 3.12 .venv` then `uv pip install --python .venv/bin/python -r requirements.txt`; no `python3 -m venv` on this VM. Browser dependencies are development-only. [§§2,14]
- Baseline full188 DB/report are immutable evidence. Non-UI working copies use SQLite's backup API from a `mode=ro` source connection. UI uses Derek's required `cp -r data/full188 data/ui188` after confirming baseline is done and has no active writer; preserve any sidecars in that directory copy and verify its188 saved page rows before serving. Copy page images/uploads and rewrite absolute image paths into the destination for UI/clean-checkout runs. Set absolute `MRT_DATA_DIR` before importing app modules. Scripts refuse a destination equal to source or an existing unrelated store. [§§5–6,14]
- One worker/CLI owns each data directory. Task2 uses `data/source188`, Task3 `data/dedupe-f1` then `data/dedupe-f2`, Task4 `/Users/dw/indigo/data/ui188` and separate task-local `data/retry-demo`, Task5 `data/release188`. Never run replay/eval while a web worker writes that same store. Eval calls `init_db()` and writes output, so it also runs against copies. No application reads gold during processing. [§§6,12,14]

## Five fixes and their measurable gates

| Fix / owner | Narrow implementation | Evidence and acceptance |
|---|---|---|
| **F1 → Task3, first deliverable** |SHA256 each stored rendered JPEG off the event loop. A nonempty complete page-hash multiset contained in another record is an exact subset; require all files/read operations to succeed and account for repeated hashes. Most pages wins canonical; publish `partial_exact`. Hashes can be computed per run; no page-table migration. |Cached bytes prove182=166,183=167,184=168. Record182–184 is contained in166–178. **Duplicates0/2→1/2,0 false** on the baseline copy. No text-only rule can override image containment. [CLAUDE §§3,8,14] |
| **F2 → Task3, after F3 output** |Both service dates known and equal, exact same record type; clinical-token word-set Jaccard≥0.8 proposes a pair; LLM must confirm complete redundancy with high confidence before grouping. Similarity alone never collapses. Compare directly to canonical; do not let a transitive chain or unknown-date bridge add members. Complementary/uncertain/distinct outputs stay separate. |Merged153–154 versus155–156 scores0.880531 with the stated scorer. Existing three-word Jaccard is0.662222, so retaining that metric with 0.8 would fail. Current split153 alone scores0.707763: F3 is required. **Duplicates1/2→2/2,0 false**;28–31 and76–78 remain distinct;151–152 and157–165 remain distinct. [§§8,12; latest user amendment] |
| **F3 → Task2** |Printed1→2 continuation plus no new-record heading and no contradictory service date defeats an unreadable fax counter/opaque header drift, even when page type drifts. Preserve explicit heading/reset and real date-change cases. Reuse/review the existing grouping patch; do not rewrite grouping. |p153 type`hospital_record`, p154 type`consultation`, p154 new-record=false, fax counter null. Existing type condition prevents printed continuity. **No p154 false start; r25=153–154; matched spans30/31→31/31, pages186/188→188/188.** Keep37–41 one record and valid admission internal splits. [§7] |
| **F4 → Task2** |Inspect original151/152, then make targeted vision refresh of at most two page units with clearer date-role/overlapping-fax instructions. Persist validated transcription/signals and regenerate151–152 summary in the copied store. Service date must come from source, never gold substitution; preserve confidence. |Both images show`Visit:10-02-2024 13:34`;152 is clearest. Saved OCR wrongly readsNovember12/October22, so summary-only rewriting is insufficient. **Original cohort dates29/30→30/30; after F3 require31/31 overall.** If image reread remains uncertain, flag and report unmet gate rather than force the value. [§6/App A–B] |
| **F5 → Task2; final measurement Task5** |Prompt forbids invented absence claims, carries a modifier only to the finding it actually qualifies, and distinguishes planned from completed actions. An explicit source negative remains reportable; missing mention is not a clinical fact. Preserve source conflicts/history and page references. Regenerate the six judged bad spans and the new153–154 span; reuse other accepted summaries. |Bad spans54–55,92–97,104–124,137–148,151–152,182–184. Fix “not stated” inventions, posterior-only displacement overgeneralization and planned reduction/splinting described as performed. **Judge unsupported6→≤2**, zero call errors, all completed summaries rejudged using unchanged judge/eval. Date-rule violations remain0. [§6/App B,§12] |

F2 scorer is explicitly changed, not the 0.8 threshold: lowercase; remove only positively identified fax/stamp lines; keep negation and clinical signs/decimals; token sets, intersection/union (empty→0). Exact tokenizer:

```python
r"[+\-−]?(?:\d+(?:\.\d+)?|\.\d+)(?:/\d+(?:\.\d+)?)?|[a-z]+(?:['’][a-z]+)?|[+\-−%°<>≤≥=]"
```

Do not feed tokens through the old punctuation-stripping normalizer afterward. On saved full-record text the PT pair scores0.811563 but dates differ; the reported0.96 boilerplate score came from different source slices/normalization and is still a required negative fixture. Complementary operative parts score0.367816. These are read-only calculations, not post-fix eval results. F2's same-type gate must be checked again after r25 is regenerated; do not weaken it to make a metric pass.

### Exact acceptance commands and planned helper contracts

Commands below run **after approval** from the named task's `build_claude/`. `scripts.replay_measured_fixes`, `scripts.check_measured_duplicates`, browser/resume tests and `scripts.clean_checkout_smoke` are **new task deliverables**, not tools claimed to exist now. Existing evaluator supports `--doc-id`, `--gold`, `--judge`, `--out`; no invented stage/subset flags are passed to it.

**S — Task2 bounded replay:** new runner backs up source into a fresh destination, reuses 188 saved images/extractions, refreshes only explicitly listed151/152, regroups, invalidates the six baseline unsupported summary spans plus newly created spans, summarizes pending rows and checks deterministic gold metrics. Date-role prompt changes affect only the targeted page refresh. It refuses a gold path as extraction input; gold is used only by its final evaluator. It writes call counts/provenance and exposes which source records changed. Tests enforce at most two vision page units, no unrequested page calls, and no edits to source DB/report.

```bash
MRT_FAKE_LLM=0 MRT_CONCURRENCY=16 .venv/bin/python -m scripts.replay_measured_fixes --source-data /Users/dw/indigo/data/full188 --data-dir "$PWD/data/source188" --doc-id d820c2727783 --refresh-pages 151,152 --baseline-eval /Users/dw/indigo/data/full188-eval.json --gold /Users/dw/indigo/eval/gold/record_1.json --out "$PWD/data/source-fixes-eval.json"
```

Do not invoke the ordinary full pipeline to perform this repair; it lacks targeted summary invalidation controls. Ordinary LLM retry policy still applies to each of the two authorized page units. This is not a third full extraction.

**D1/D2 — Task3 duplicate checks:** new helper copies the source store, runs only the chosen dedupe mode, invokes the existing evaluator, and exits nonzero unless duplicate metrics and named negative-pair assertions pass. `--mode exact` makes no LLM calls; `--mode confirmed` uses existing real adapter for eligible pairs, at most8 new confirmations in this pass. It records model/prompt/candidate scores and decisions locally, without putting medical text in Git.

```bash
MRT_FAKE_LLM=0 .venv/bin/python -m scripts.check_measured_duplicates --source-data /Users/dw/indigo/data/full188 --data-dir "$PWD/data/dedupe-f1" --doc-id d820c2727783 --mode exact --expect-hits 1 --gold /Users/dw/indigo/eval/gold/record_1.json
MRT_FAKE_LLM=0 .venv/bin/python -m scripts.check_measured_duplicates --source-data /Users/dw/indigo-source-fixes/build_claude/data/source188 --data-dir "$PWD/data/dedupe-f2" --doc-id d820c2727783 --mode confirmed --expect-hits 2 --gold /Users/dw/indigo/eval/gold/record_1.json
```

F1 is measured before F2. Use positive near/exact fixtures plus negative boilerplate, complementary, missing-image and conflicting-evidence tests. Preserve all copied source rows and both operative reports. `Show duplicates`/collapse changes presentation, not stored records.

**U — Task4 UI on full188:** browser suite reads `MRT_UI_DATA_SOURCE`, makes its own consistent disposable copy, starts/stops the real app on a free port and tests existing screens. It must use the current live API, not the parked mock server. Tests cover all 188 source pages accessible, expected cards/filters, page154/151 source inspection, duplicate-toggle behavior, two small synthetic uploads, polling while typing, Save/refresh/restart/Reset and safe keyboard navigation. No API rebuild. Actual full188 duplicate badges are rechecked in Task5 using accepted Task3 data; baseline full188 has no duplicate groups.

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
MRT_UI_DATA_SOURCE=/Users/dw/indigo/data/full188 MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q tests/test_ui_browser.py tests/test_resume_demo.py
```

Browser tests require an installed browser; `MRT_TEST_BROWSER` may name its executable. If unavailable, record the exact blocker and execute the same manual checklist; never label manual work as an automated pass. Mandatory Mac walkthrough uses a server on the VPS bound to127.0.0.1:8000 with Task4's full188 copy and its branch's static files:

```bash
# VPS, from /Users/dw/Indigo-ui/build_claude after preparing data/ui188:
MRT_DATA_DIR="$PWD/data/ui188" MRT_MAX_PAGES=0 MRT_PORT=8000 .venv/bin/python -m app
# Mac; keep this terminal open, then open http://127.0.0.1:8000:
ssh -N -L8000:127.0.0.1:8000 vps
```

For retry use a **different fresh store**, synthetic12-page PDF, `MRT_FAKE_LLM=1 MRT_CONCURRENCY=1 MRT_FAIL_AT_PAGE=5 MRT_PORT=8006`. Test4 saved page results before failure, Retry in the **same server process**, completion12/12, and unchanged attempt counts/model-call spy counts for pages1–4 and previously completed summaries. Separately test restart of interrupted work and persistence of edits. The hook is process-local: do not restart between injected failure and Retry, and do not try injecting against the already-complete full188 DB. Write exact pass/fail evidence in UI_REHEARSAL.md. [CLAUDE §§6,9–11]

**J — Task5 final existing evaluator, after merge:** prepare `data/release188` from Task3's accepted copy using SQLite backup; verify all copies share source PDF SHA and preserve original baseline report. Stop the worker while checking. Rerun judge once across all completed records with existing unchanged judge prompt; do not improve the number by omitting inconvenient records/claims. If unsupported>2, return only failing spans to Task2's owner and run the judge again within reserve; report all attempts.

```bash
MRT_DATA_DIR="$PWD/data/release188" MRT_FAKE_LLM=0 MRT_CONCURRENCY=16 .venv/bin/python eval/run_eval.py --doc-id d820c2727783 --gold /Users/dw/indigo/eval/gold/record_1.json --judge --out "$PWD/data/post-fixes-eval.json"
```

Task5 compares actual JSON with every gate in the baseline table, including unchanged type/date-rule quality and zero false duplicate pairs. Publish only aggregate before/after numbers in README; raw medical eval/DB/images stay ignored. Record31/31 as matched gold spans, actual predicted count, date denominator and judge counts. The existing judge has input-length limits; do not claim it validates every fact on every original image.

**C — Task5 clean-checkout smoke:** script takes a committed candidate, clones into a new temporary directory, creates a venv with`uv`, installs checkout requirements, generates synthetic PDFs, launches the server with fake model/free port/fresh data, asserts upload→done→timeline→PATCH→restart→persist→reset and retry behavior, then exits nonzero on any failure. It cannot import code/venv/data from the source checkout or require real samples/API key. Retain concise command/exit evidence; cleanup only its own processes/temp files.

```bash
MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q
CANDIDATE_SHA=$(git rev-parse HEAD)
.venv/bin/python -m scripts.clean_checkout_smoke --repo /Users/dw/indigo-integration --commit "$CANDIDATE_SHA"
```

Commit the smoke script before running it against that SHA. `uv venv --python 3.12 .venv` and `uv pip install --python .venv/bin/python -r requirements.txt` execute inside the clone's `build_claude/`. Full188 UI and paid judge are separate checks; a synthetic clean-checkout pass does not replace them.

### Task5 README and rehearsal output

README includes quick start using`uv`; app root; key/model configuration; local storage plus OpenAI processing; baseline/final SHA and before/after metrics; scoped rerun procedure; known assumptions/flags; feature status and any failed gates. Preserve unresolved restrictions/laterality/date-source conflicts as reviewer flags rather than silently resolving them.

Ten-click/action demo, rehearsed by 16:10: open warmed188-page timeline; show progress/source count; select37–41 as one record; inspect151/152 date against image; show exact subset copy; show confirmed near copy with both sources; show the distinct PT visits and complementary operative parts; edit/refresh/restart/Reset; use separate retry tab and resume; open README before/after results. Keep Mac tunnel alive and all demo stores prepared; do not run188 vision calls during the demo.

## Cuts and deferred work

- **Cut the broad foundation rewrite:** no new schema, retirement, fingerprints, scope fields, response-model replacement, mock API, adapter overhaul or endpoint/worker redesign. Current code already delivers the core flow; parked work is not the release base.
- **Cut new extraction/full-run work:** reuse188 cached pages; permit only 151/152 targeted refresh, affected summary regeneration, bounded duplicate confirmations and the final judge. No new OCR engine/model/provider, DPI sweep or concurrency tuning.
- **Cut new eval harness/metrics redesign:** preserve scoring and judge prompt for a comparable before/after measurement. Keep its coverage limits visible; do not silently improve denominators or rewrite gold.
- **Cut new grouping/date/type algorithms:** apply only the measured fax-counter/type-drift and source-date repairs; keep high-scoring behavior and regression fixtures.
- **Cut UI redesign and extra features:** no framework, auth, cloud, export, new sort/delete features, or additional filters. Existing working extras may remain; do not spend time deleting them merely to match an older cut list.
- **Defer automatic complementary linking and broad near matching:** retain both operative parts; implement only requested candidate rule and confirmations. No fuzzy grouping across unknown dates/types or transitive similarity chains.
- **Keep the judge, UI, retry, source images, README and clean-checkout gate.** They are explicitly required now. If F2 misses the 15:50 gate, retain measured F1 and show near copies separately, report duplicates1/2 as an unmet requested target; never claim completion by quietly cutting it.

## Exact launch prompts — held until a new go

These blocks are the contents for`prompts/task-N.md` if materialized after approval. Prompts are materialized at launch; latest guardrails above override their historic stop/push wording. Codex workers use`gpt-6-astra` explicitly: high for source/dedupe/recovery, medium for agreed UI implementation, ultra for coordination/final judgments. Application model stays`gpt-6-luna`.

### prompts/task-1-baseline.md

```text
You are claude-main. Execute revised TASKS.md Task1 only after Derek's new go. Baseline is claude-build 5048b89, following 16e37fe; 66 offline tests and working pipeline/API/eval already exist. In /Users/dw/indigo-release on main, safely fast-forward merge the pinned baseline, install via uv, run the 66-test suite and record BASE_SHA/interface freeze in TASKS.md. Do not switch/reset the live indigo checkout, consume its uncommitted grouping patch, or merge consolidate/foundation. Your only authored file is root TASKS.md. Hand the grouping patch/review findings to Task2's owner. No new schema or application code. Return SHA, command results and exact lane launch base; stop.
```

### prompts/task-2-source.md

```text
You are claude-A. After the new go and Task1 barrier, work on fix/measured-source at /Users/dw/indigo-source-fixes from supplied BASE_SHA. Read revised TASKS.md, CLAUDE sections6/7/12/14 and appendices. Own only Task2's listed files. Review/preserve the existing grouping patch and apply F3 narrow printed-continuation fix; inspect151/152 and perform at most two targeted vision page refreshes for F4 on a copied DB; tighten summary prompt for F5 and regenerate six judged failing spans plus new spans. Build only the bounded replay helper/tests, not a new pipeline/API/evaluator. Use immutable full188 baseline and gold only for evaluation; no hard-coded production page/date answer. Run Task2 offline tests and command S. Require31/31 matched spans,188/188 pages,31/31 dates/types,0 date violations; leave final judge to Task5. Return code SHA, data/source188 handoff, calls made and unresolved flags. Stop.
```

### prompts/task-3-dedupe.md

```text
You are codex-review using gpt-6-astra at high effort. After go/Task1, work on fix/measured-dedupe at /Users/dw/indigo-dedupe from supplied BASE_SHA. Own only Task3 files. Deliver F1 first: complete JPEG-hash containment, longest canonical, partial_exact; command D1 must yield1/2 duplicate hits,0 false. Then implement same-known-date/same-type clinical-token unigram Jaccard>=0.8 candidates and mandatory gpt-6-luna confirmation via existing llm.text_json. Preserve uncertain/complementary/distinct copies and all sources. No schema/API/adapter/eval changes. Merge Task2's accepted source commit and consume its data/source188 copy before D2; target2/2,0 false; PT28–31vs76–78 and operative151–152vs157–165 remain separate. Run owned tests plus existing eval tests. Return SHA, D1/D2 reports, confirmation evidence/counts and data/dedupe-f2 handoff. Stop.
```

### prompts/task-4-ui-retry.md

```text
You are codex-ui using gpt-6-astra at medium effort for UI, high for recovery investigation. After go/Task1, merge supplied BASE_SHA into existing ui at /Users/dw/Indigo-ui without reset. Own only Task4 files. Reuse current UI and real API, test against a consistent full188 copy, and make only demonstrated UI fixes. Add browser/resume tests and dev-only browser dependency; prove saved edits through restart/reset, source navigation, duplicates toggle and polling while typing. Run U and retry tests, then the mandatory Mac ssh -N -L8000:127.0.0.1:8000 vps walkthrough. Retry uses fresh synthetic store, fake model, concurrency1, fail at5 and same-process Retry; successful page calls must not repeat. Never alter baseline full188 DB or rebuild API/worker. Record automation blockers/manual evidence honestly in UI_REHEARSAL.md; return SHA/results and stop.
```

### prompts/task-5-integration.md

```text
You are claude-main integrating at /Users/dw/indigo-integration on consolidate/demo. Start from Task1 BASE_SHA; merge accepted Task2,Task3,Task4 in order. Edit only Task5 files; return code defects to owners. Reuse accepted repaired data via safe SQLite backup. Run the full offline suite, existing evaluator with --judge (J), and uv-based committed clean-checkout smoke (C). Require the revised metric table, unsupported<=2 with0 judge errors, and successful UI/retry repetition on this SHA. Write aggregate before/after README, assumptions, exact evidence/commands and10-action demo; rehearse with Mac tunnel. No extra full188 extraction, no gold changes or metric redefinition, no broad foundation merge. Finish by 16:10, keep reserve to 16:20, and report unmet gates plainly. Return final SHA/artifacts; checkpoint pushes are authorized, deployment is not.
```

**Execution approved. Launch in tmux, checkpoint/push every10min, start rolling integration at15:20, and freeze at16:20.**


## Task1 verified baseline handoff — October 13

- Runtime BASE_SHA: `5048b89f412b8c76fd8d91819469f7fdc8a0d7ac`. Release branch `main` was already at this exact commit; `git merge --ff-only 5048b89f412b8c76fd8d91819469f7fdc8a0d7ac` returned `Already up to date.`
- Dependency check from `build_claude/`: `uv pip install --python .venv/bin/python -r requirements.txt` exited 0 (10 packages checked).
- Baseline verification from `build_claude/`: `MRT_DATA_DIR=/Users/dw/indigo-release/build_claude/data/task1 MRT_FAKE_LLM=1 .venv/bin/python -m pytest -q --basetemp=/Users/dw/indigo-release/build_claude/data/task1/pytest-baseline` exited 0: **66 passed in 0.23s**. Initial invocation had 59 passed/7 fixture setup errors because the task-local temporary parent directory did not exist; creating that directory resolved all errors without code edits.
- Interface freeze: the Frozen interfaces and data handling section above is binding. No schema, API, model/DAO signatures, adapter, worker, or runtime source changes were made by Task1.
- Exact lane launch BASE_SHA is the committed and pushed documentation checkpoint recorded in `/Users/dw/indigo-task-control/BASE_READY.json`. Its application tree is identical to the pinned runtime BASE_SHA above; the JSON is published atomically only after push succeeds.
- Task2 owns review/application of `/Users/dw/indigo-task-control/source-grouping.patch` and the F3/F4/F5 findings above. The live root grouping changes were not consumed or modified by Task1.
- No real model calls, extraction, full188 evaluation, browser rehearsal, or clean-checkout release smoke were performed by Task1. Those remain the assigned downstream lanes' checks. The expected external adversarial review was not yet present at the baseline check; Task5 remains its owner.
