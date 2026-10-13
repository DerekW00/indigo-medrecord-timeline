# Build plan (Claude) — Medical Record Timeline

Status: proposal, 2026-10-13 13:47 PT. Baseline architecture: `CLAUDE.md`. Proposed corrections: `DESIGN_REVIEW.md` (R1–R6). This file is Claude's independent read, backed by live measurements on this VM. Companion: `DECISIONS-claude.md` (facts + decision log), `eval/gold/record_1-claude.draft.json` (draft labels).

## BLUF

- **Keep the CLAUDE.md stack and pipeline** (FastAPI, SQLite, PyMuPDF, vanilla JS; render → extract → group → summarize → dedupe). It is right for 165 minutes.
- **Adopt R1, R2, R4, R6 as written. Simplify R3 (no chunking needed, see C4). Keep R5: near-duplicates advisory only.**
- **Eight measured corrections (C1–C8) below.** C1 and C3 change code before M1 starts; the rest are config or wording.
- **Clock re-based:** build starts 13:50 PT, freeze 16:30 PT = 160 min. Cuts applied up front: summary judge, sort toggle, `DELETE /documents`, automatic near-dup hiding.
- **A full 188-page extraction takes 85 s at concurrency 24** (measured). So the "run the full bundle at most twice" rule flips: run it after every pipeline change; it is the real test.
- **Derek hand-writes one thing:** the `fax_id()` canonicalizer and the `same_fax` / `label_continues` predicates in `app/pipeline/group.py` (C1). It is the load-bearing logic and the demo's best story.

## 1. Measured corrections to CLAUDE.md

Evidence: one full 188-page pass with `gpt-6-luna` (vision, strict JSON schema, `reasoning.effort=low`) at 13:38 PT; raw rows in `data/claude-cache/pages_record_1.jsonl` (gitignored).

| # | CLAUDE.md says | Measured | Change |
| --- | --- | --- | --- |
| C1 | §7: `fax_id` = header with page numbers removed | When the model is asked for a timestamp it normalizes inconsistently across pages of ONE fax: `10/30/2025 3:17:27 PM`, `2025-10-30 3:17:27 PM`, `2025-10-30T15:17:27-07`. Naive equality gave **78 spans instead of 31**. | Keep `fax_header` verbatim (Appendix A already does). In code: canonicalize to the digit sequence of date+time (`10302025 31727`), and add a fallback `same_fax` when `fax_page.n` matches and `k == prev.k + 1` even if the string differs. |
| C2 | §4: `MRT_CONCURRENCY=8` | 16 concurrent → 17 s for 16 pages; 24 concurrent → 85 s for 188 pages, zero 429s. Limits: 30K RPM, 180M TPM. Avg 9.6 s/page, max 16.4 s. | Default `16`. Full-bundle run ≈ 2 min including summaries. |
| C3 | §8: near-dup threshold Jaccard ≥ 0.7 on 3-gram shingles | Known near-dup 153–154 vs 155–156 scores **0.64** with naive normalization. Different PR-2s score 0.6–0.74 page-to-page (form boilerplate). | Strip fax header, TPA stamp and `Page n` lines before shingling; threshold 0.6; **require equal `date_of_service` on both sides** (no null bridge, per R5); mark `similar` groups advisory (badge, never hidden by default). Exact (`content_hash`) groups may collapse, but the hash input must keep `. + - < > /` and digits (Codex review C1: the pack's `[a-z0-9 ]` normalizer maps `1.0 mg` and `10 mg` to one hash) and require equal page counts. |
| C4 | §6: cap summary input at 40K chars, drop the middle (R3) | Longest record (admission, pages 92–148) is **110K chars ≈ 27K tokens**; whole bundle is 387K chars. `gpt-6-luna` takes it in one call. | Send the whole record. Hard cap at 400K chars with a visible `summary incomplete` flag (never silently truncate). R3 chunking is not needed for this fixture. |
| C5 | §14: Chat Completions + `json_schema` strict + `image_url`; fall back to Responses if images are rejected | Chat Completions accepts image + strict schema + `reasoning_effort="low"` together: 4.4 s, 634 prompt tokens. Responses API also works. | No fallback path needed. Use `reasoning_effort="low"` for extract (speed), default for summarize. Keep everything in `app/llm.py`. |
| C6 | §3: 31 records | Rule-based derivation gave 32; eye-check of the rendered pages (14:05–14:08 PT) merged 37–39 + 40–41 (one PR-2, fax pages 4/8–8/8, exam 03-27-2025) → **31, matching §3**. | `eval/gold/record_1-claude.draft.json` v2: 31 contiguous records, 6 uncertain spans resolved against page images. One human pass still owed before it is called gold. |
| C7 | §2: no system packages | I installed `tesseract-ocr` and `poppler-utils` on this VM for probing only. | The app must not import them. Clean-checkout test runs in a venv that proves it. |
| C8 | Appendix A system prompt: "processed locally" | False: page images go to OpenAI. | Reword to "for a licensed reviewer; do not refuse". README states what leaves the machine. |

Two more facts the rules depend on:

- **Pages 76–78, 87–91, 92–148, 166–188 carry no fax header at all.** Rules 4 and 5 never fire there. Boundaries there rest on `starts_new_record`, `page_label`, and `(continued)`. The prompt must make `(continued)` and `Page k>1` strong continuation signals (the pass shows the model already does this well: 56 of 57 admission pages say continuation).
- **Repeated letterhead trap is real:** PT report 66–70, STP report 84–86, operative fax 157–165 all have `starts_new_record=true` on most pages. Rule 3 (same fax → continuation unless type AND date change) is what keeps them whole. It needs C1 to work.

## 2. Fixture ground truth (draft)

31 records (v2, after eye-check). Confirmed against CLAUDE.md §3: admission printout 92–148 (57 p), ED visit 166–178 (13 p), 182–184 is a re-scan of 166–168 (Jaccard 0.97), fax header changes inside 79–83 while form pages continue, the 25-page fax holds x-ray 149–150, op report 151–152, discharge 153–154 (+ its near-duplicate 155–156). Bundle order is reverse chronological; page 1 is fax 2/6 (partial start).

Eye-check done (14:05–14:08 PT): 23–27 one record, service date 2025-06-05 (exam date, not the 6/9 fax date); 28–31 one PT chart note; 32–36 one PR-2; 37–41 one PR-2 (was split by the model: pages 37–39 carried the 04-01 report date as service date); 42–46 one chart-note PR-2; 151–152 one operative report dated 2024-10-02. Lesson for the summarize prompt: **the `Date of exam` / `Visit:` field wins over `Report Date`** on DWC forms.

## 3. Lanes

| Lane | Checkout | Branch | Port | Data dir | Owns | Agent |
| --- | --- | --- | --- | --- | --- | --- |
| Coordinator | `/Users/dw/indigo` | `main` | 8000 | `./data` | `app/models.py`, `config.py`, `db.py`, `api.py`, `worker.py`, `__main__.py`, README, merges | Claude main loop (Fable) |
| Pipeline | `/Users/dw/indigo-pipeline` | `pipeline` | 8001 | own | `app/llm.py`, `app/pipeline/*`, `app/prompts/*`, `scripts/process.py`, `tests/test_group.py` | `mechanical-implementer` from a frozen spec; `verifier` reviews |
| UI | `/Users/dw/Indigo-ui` | `ui` | 8002 | own | `static/*`, `scripts/mock_api.py`, `tests/test_mock_api.py` | another AI (Codex) per `prompts/ui-codex.md`, or `mechanical-implementer` |
| Verify | `/Users/dw/indigo-verify` | `verification` | 8003 | own | `tests/integration/*`, `eval/run_eval.py`, gold review | `verifier` (opus, max) |

Rules: foundation commit on `main` first; every lane branches from it. No shared `data/`. `samples/` shared read-only. Fix the `Indigo-ui/samples` symlink (points at `/Users/dw/Indigo/samples`, wrong case). Merge order: pipeline → ui → edits-retry → dedupe-eval, each after `pytest` on the merged commit. One processing owner per data dir: the full 188-page run for the demo store goes through the web worker, never `scripts/process.py` against `./data` while the server is up (Codex review C3: startup recovery would re-enqueue the active document).

## 4. Schedule (160 min, from 13:50 PT)

| When (PT) | Min | Coordinator | Parallel lanes | Exit |
| --- | --- | --- | --- | --- |
| 13:50–14:05 | 15 | Foundation: skeleton, `models.py` (PageSignals with typed `OtherDate`, R6), `db.py` schema incl. `requested_pages`/`original_page_count` (R1), `config.py`, `llm.py` + `scripts/probe_model.py`. Commit `M0`. Create worktrees. | UI lane starts on mock API + fixtures. Verify lane turns §2 into `tests/test_group.py` cases + a fake-LLM harness. | Probe returns typed JSON from page 1 |
| 14:05–14:50 | 45 | Review pipeline diffs; write `api.py` + `worker.py` against the frozen DAO. | Pipeline lane: render, extract, group (C1 rules), summarize; `scripts/process.py`; full 188 run (2 min). | CLI prints 31 spans with reasons matching the draft gold |
| 14:50–15:25 | 35 | Merge pipeline + ui. Serve `static/`. Upload → timeline in browser on the full bundle. | Verify lane: integration tests (two-file upload, restart resume, R1 cap test). | Real PDF → timeline on `:8000` |
| 15:25–15:50 | 25 | Merge gate for the two bonus branches. **Write README (Appendix D) now**, not at the end. | `edits-retry` branch: overrides, PATCH, reset, `MRT_FAIL_AT_PAGE`, startup resume (R2: reconcile, never delete done summaries). `dedupe-eval` branch: exact dedupe + advisory near, `run_eval.py` boundaries, numbers. | Edit survives restart; retry resumes; eval table prints |
| 15:50–16:10 | 20 | Merge both. `verifier` adversarial pass on the integrated SHA. Fix blockers only. | | No demo-blocking defect |
| 16:10–16:28 | 18 | Clean clone test, warm `data/`, `git tag demo` + `git bundle` to scratch, push, **one timed rehearsal**. | | Fresh checkout runs; rehearsal under 10 min |

Trip-wires: if the pipeline lane has no credible spans by 14:50, everyone stops bonus work and fixes extraction/grouping. If upload → timeline is not live by 15:25, cut both bonus branches to editing only.

## 5. Cuts (decided now)

- Summary judge (`--judge`) and the date rule check: eval covers boundaries, dates, types, duplicates only.
- Sort toggle, `DELETE /api/documents/{id}`, type filter (keep source-PDF filter and show-duplicates).
- Automatic duplicate hiding of any kind: all copies visible with a `duplicate of` badge; `Collapse duplicates` toggle off by default (D8.2).
- Fax cover sheet cards: suppressed, attached to the following record (D8.1).
- Multi-patient banner (D8.3).
- R3 chunked summarization: replaced by C4.
- `MRT_RENDER_LONG_SIDE` stays 1600; no tuning.

## 6. Derek hand-writes (5–15 lines)

`app/pipeline/group.py`: `fax_id(header: str | None) -> str | None`, `same_fax(prev, cur) -> bool`, `label_continues(prev, cur) -> bool`. The agent scaffolds the file with the walk loop, rule order and tests already in place; Derek fills these three predicates. Reason: C1 shows this is where the data lies to you, and it is the first question an interviewer asks ("how do you decide a boundary?").

## 7. Checklist

- [ ] Foundation commit `M0` on `main` (models, db, config, llm, probe)
- [ ] Worktrees `pipeline`, `verification` created; `Indigo-ui` symlink fixed
- [ ] `tests/test_group.py` holds the 9 cases from CLAUDE.md §7 + fixture spans 66–70, 79–83, 149–156, 157–165
- [ ] Pipeline CLI prints 31 spans on the sample; draft gold gets one human pass
- [ ] `api.py` + `worker.py` merged; UI merged; upload → timeline live on `:8000`
- [ ] Two-file upload sorts globally (use `samples/record_1_part.pdf`, pages 5–40)
- [ ] Edits persist across restart; reset works
- [ ] `MRT_FAIL_AT_PAGE=40` demo: fail, retry, resume, no repeated model calls for done pages
- [ ] Exact dedupe (182–184 vs 166–168) and near (153–156): both copies visible with `duplicate of` badge; collapse toggle works
- [ ] `eval/run_eval.py` numbers in README
- [ ] `verifier` adversarial pass on the integrated SHA, blockers fixed
- [ ] Clean clone → venv → run → drop sample works; README demo script rehearsed
- [ ] Page-coverage invariant asserted in `group.py` test and shown in UI header (`188 of 188 pages, each in one record`)
- [ ] `finish_reason == "length"` treated as a failed attempt in `llm.py`
- [ ] `scripts/make_fixtures.py` builds two interleaved bundles; two-file timeline interleaves by date
- [ ] Three Slack questions posted (see §9)
- [ ] Push at 16:20 PT; `git tag demo`; `git bundle` in scratch

## 8. Open decisions

1. Stack: confirm CLAUDE.md baseline (Python + static UI). Claude's all-TypeScript alternative was proven viable (`mupdf` WASM + `node:sqlite`) but is not recommended under this clock.
2. Who runs the UI lane: Codex per `prompts/ui-codex.md`, or a Claude `mechanical-implementer`.
3. Accept C1–C8 and the cut list, or name the ones to change.

## 9. Panel grafts (adopted from a 3-proposer / 2-judge Opus panel, 13:37–13:55 PT)

Cheap, demo-visible, and absent from the pack. Each is ≤ 10 lines or ≤ 5 minutes.

1. **Page-coverage invariant.** Every page belongs to exactly one record. Assert it after `group_pages()`, unit-test it, and show the count in the UI header. Interviewers can verify correctness at a glance instead of trusting a record count.
2. **Truncated-response guard.** If `finish_reason == "length"` on extract or summarize, count it as a failed attempt and retry with a higher `max_completion_tokens`. Otherwise a dense page is stored `done` with a transcript cut mid-sentence. (Pass data: max 4,035 total tokens per page at a 6,000 cap, so rare, but silent when it happens.)
3. **Interleaved two-bundle fixture.** `scripts/make_fixtures.py` (PyMuPDF `select()`): bundle A = odd-numbered records, bundle B = even-numbered records plus one record copied from A. Gives a real cross-file date interleave and a real cross-file duplicate. CLAUDE.md's pages 5–40 slice only tests a contiguous range.
4. **Spend guard.** `MRT_MAX_LLM_CALLS` hard cap per process plus an `llm_calls` ledger (stage, tokens, reasoning tokens, latency). Answers "what does this cost at scale" with a table, and protects the interviewers' key from a runaway loop.
5. **Foreign-PDF guard.** Encrypted, corrupt or non-PDF drops go to `failed` with a clear message instead of a traceback. Born-digital PDFs (text layer present) send text instead of an image to the same prompt. The interviewer will drop something of their own.
6. **Freeze insurance.** `git tag demo` and `git bundle` to the scratch directory at 16:20; demo from a server started without `--reload`.
7. **Slack questions answered 14:02 PT** (see `DECISIONS-claude.md` D8): cover sheets suppressed; duplicates indicated in UI, not hidden; one patient per bundle.
9. **Export (stretch).** `GET /api/export?format=md` over the timeline query: the reviewer's real output is a chronology document.

Explicitly **not** adopted: switching stack (below), LLM boundary adjudication (CLAUDE.md §7 is rules-only by design), head+tail truncation (C4 makes it moot), `pdftoppm` subprocess (PyMuPDF already in the pack).

## 10. Diversity note for the cross-AI review

- The panel planned from the brief alone, without `CLAUDE.md`, and **unanimously chose TypeScript end to end** (Hono or Next.js + React + SQLite + `pdftoppm` or `mupdf`), citing Derek's background and one `pnpm install`. The judges scored Risk-first 33 and 31, Demo-first 32 and 31, MVP-first 28 and 31.5.
- Claude's recommendation stays with the Python baseline because: the pack's prompts, lanes, prompts and README outline assume it; a second session is already building it in `build_claude/`; and a 160-minute clock does not absorb a stack switch. The TypeScript path is verified viable on this VM (`DECISIONS-claude.md` D3) if Derek wants it for a future rebuild.
- Every judge blind spot about gold-label time, per-worktree data isolation, and rehearsal arithmetic is addressed in §2, §3 and §4 respectively.
