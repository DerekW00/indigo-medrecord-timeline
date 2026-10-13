Read CLAUDE.md in full before you do anything else. It is the context pack for this build. Its decisions are final. If one blocks you, make the smallest change that unblocks you and record the assumption in the README section "Assumptions".

Situation: timed build, code freeze 16:30 PT today. Working features only. The sample bundle is samples/record_1.pdf (188 scanned pages, no text layer). OPENAI_API_KEY is in the environment. The model is gpt-6-luna. A virtualenv with requirements.txt already exists in .venv.

You own: app/, scripts/, tests/, README.md. Another agent builds static/ (sections 9 and 10) on branch "ui" in a separate worktree and tests it against its own mock API. Do not create files under static/. The API contract in section 9 is fixed; implement it exactly so the UI plugs in without changes.

Order of work, commit at the end of each milestone with the milestone name in the message:
1. M0: app/config.py, app/llm.py, scripts/probe_model.py. Send page 1 of the sample as an image and ask for JSON. Confirm image input and JSON output work with this model and adapt llm.py to what the API accepts.
2. M1: app/db.py, app/pipeline/*, app/worker.py, scripts/process.py. Run MRT_MAX_PAGES=12 on the sample. Print the spans with boundary reasons, titles and dates. Render pages 1 to 12 and look at them. Compare. Fix rules or prompts before moving on. Write tests/test_group.py with the cases in section 7. Then start the full 188-page run in the background with nohup and keep working.
3. M2: app/api.py and app/__main__.py per section 9. When the "ui" branch is merged into main (I will tell you), serve static/ and verify the page works against the real store.
4. Stop after M2 and report. Phase 2 (editing, retry, dedupe, eval) runs in separate worktrees.

Rules: develop with MRT_MAX_PAGES=12; run pytest before each commit; one page failure never kills a document, one document failure never kills the worker; keep every model call in app/llm.py; log every model call in one line; do not ask me questions unless blocked, make the call and write it in README "Assumptions".

After each milestone report in at most 12 lines: Works (with the command or URL to see it), Next, Assumptions, Time (now, and ahead or behind the section 13 time boxes). If you are more than 20 minutes behind, apply the cut list at the end of section 13 and say which cuts you applied.

Start with M0 now.
