Read AGENTS.md first. You are a reviewer, not an implementer. Do not edit files.

Review the current diff on main against the context pack: git diff <context-pack-commit>..HEAD. Check sections 6 (pipeline), 7 (grouping rules), 8 (duplicates) and 14 (engineering rules) line by line against the code.

Look for, in this order of importance:
1. Checkpoint gaps: any unit of work (page, record) whose result is not written to SQLite the moment it is produced, or any stage that does not skip units already done.
2. Silent failures: exceptions swallowed without a status write and a log line; a page failure that kills a document run; a document failure that kills the worker.
3. Event loop blocking: PyMuPDF rendering, hashing, or sqlite calls in the async path without to_thread, or a sync OpenAI client.
4. Grouping rule mismatches between section 7 and app/pipeline/group.py, including rule order and the partial_start rule.
5. Prompt drift: differences between app/prompts/*.md and the appendices.
6. JSON validation gaps: model output used without pydantic validation and retry.

Output: a ranked list of findings. Each finding: file and line, what is wrong, why it matters, the smallest fix. Then a one-line verdict: merge, or fix first. No praise, no summary of what the code does.
