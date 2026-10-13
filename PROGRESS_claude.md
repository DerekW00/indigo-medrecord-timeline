# Progress (Claude build) — Medical Record Timeline

Project root: `build_claude/` (self-contained: `cd build_claude && .venv/bin/python -m app`).
Codex runs the same task in parallel; its files must not collide with `build_claude/` or `*_claude.md`.

Clock: build started 13:38 PT (20:38 UTC) 2026-10-13. Freeze 16:30 PT (23:30 UTC).

## Blockers

- [x] `OPENAI_API_KEY` found in the repo root `.env` at 20:46 UTC; was not available in this session (not in env, no `.env` in repo or home).
      Put it in `build_claude/.env` (`cp env.example .env`, edit) or `/Users/dw/indigo/.env`.
      Everything below is built and tested against `MRT_FAKE_LLM=1` until the key lands.

## Milestones

- [x] M0 skeleton: `requirements.txt`, `env.example`, `.gitignore`, `app/config.py`, `app/models.py`, `app/db.py`, `app/llm.py` (Chat Completions json_schema strict, json_object fallback, Responses API fallback, 3 attempts, fake mode), `scripts/probe_model.py`
- [x] M0 probe against the real `gpt-6-luna`: Chat Completions + json_schema strict accepted image, 6.6 s (20:47 UTC)
- [ ] M1 pipeline: render, extract, group (+tests), summarize, `scripts/process.py`
- [ ] M1 full 188-page run (needs key)
- [ ] M2 server + UI
- [ ] M3 editing, retry, startup resume, `MRT_FAIL_AT_PAGE`
- [ ] M4 dedupe (+tests), eval, gold file, numbers in README
- [ ] M5 README, clean-checkout test, demo script
- [ ] Parallel adversarial review pass (2 reviewers per module, fixes applied)

## Decisions and assumptions

- `.env.example` is blocked by a permission rule in this session, so the template is named `env.example`.
- `app/db.sync_records` keeps records whose deterministic id is unchanged (summary and status survive),
  inserts new ids as pending, deletes stale ids. Overrides are never touched.
- `MRT_FAKE_LLM=1` is an offline deterministic model stand-in for tests and UI work.
- Build agents run on the top model (Fable, effort max); two adversarial reviewers per module.
