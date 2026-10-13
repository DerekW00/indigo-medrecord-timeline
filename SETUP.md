# Indigo build: VPS setup in 5 minutes

Files from this pack: `CLAUDE.md` (context pack), `bootstrap.sh` (one-time setup), this file.

## 1. Copy the pack to the VPS (from your Mac)

```bash
scp ~/Downloads/CLAUDE.md ~/Downloads/bootstrap.sh ~/Downloads/SETUP.md <user>@<vps>:~/Indigo/
```

## 2. Bootstrap (on the VPS, once)

```bash
cd ~/Indigo && bash bootstrap.sh
cp .env.example .env && nano .env        # paste the OpenAI key
```

What it does: moves the PDFs into `samples/`, writes `.gitignore`, `.env.example`, `requirements.txt`, `.claude/settings.json` (no permission prompts) and `AGENTS.md` (the same pack for Codex), writes the five session prompts into `prompts/`, commits, creates the `ui` worktree at `~/Indigo-ui`, and installs a venv in both.

## 3. Launch the Phase 1 sessions

Export the key in every shell that launches an agent:

```bash
cd ~/Indigo && set -a && . ./.env && set +a
```

tmux layout (`Ctrl+b` is the prefix):

```bash
tmux new -s build
# Ctrl+b %   -> right pane
# Ctrl+b "   -> split the right pane top and bottom
```

Left pane, Claude Code on main (pipeline, M0 to M2):

```bash
cd ~/Indigo && set -a && . ./.env && set +a && . .venv/bin/activate
claude "$(cat prompts/main-claude.md)"
```

Right top, Codex on the ui worktree (front end plus mock API):

```bash
cd ~/Indigo-ui && set -a && . ~/Indigo/.env && set +a && . .venv/bin/activate && export MRT_PORT=8001
codex --full-auto "$(cat prompts/ui-codex.md)"
```

(If your Codex version uses other flags, the intent is: auto-approve edits and commands inside the workspace, no sandbox escape needed.)

Right bottom, a shell for you: `git log --oneline --all`, `pytest`, and the Codex review later.

If Claude Code still prompts for permissions, restart it with `claude --dangerously-skip-permissions "$(cat prompts/main-claude.md)"`. The VPS is disposable, so that is acceptable here.

## 4. About 14:15, review M1 with Codex (same main folder, read only)

```bash
cd ~/Indigo && codex "$(sed "s/<context-pack-commit>/$(git log --reverse --format=%h | head -1)/" prompts/review-codex.md)"
```

Give the findings to the Claude session as one message: "Fix these, in order, then re-run pytest and the 12-page run: ..." Skip anything you disagree with and say why in the message.

## 5. About 14:30, merge the UI and wire the real API

```bash
cd ~/Indigo && git merge --no-ff ui -m "merge: ui"
```

Then tell the main Claude session: "The ui branch is merged. Do M2 step 3: serve static/ from app/api.py and verify the real timeline on the 12-page run at http://127.0.0.1:8000."

## 6. About 14:45, Phase 2 worktrees from the merged main

```bash
cd ~/Indigo
git worktree add -b edits-retry ../Indigo-edits main && ln -sfn ~/Indigo/samples ../Indigo-edits/samples
git worktree add -b dedupe-eval ../Indigo-eval main && ln -sfn ~/Indigo/samples ../Indigo-eval/samples
for d in ../Indigo-edits ../Indigo-eval; do python3 -m venv $d/.venv && $d/.venv/bin/pip install -q -r requirements.txt; done
cp -r data ../Indigo-eval/data        # warm database for the eval, no second 188-page run
```

Two new tmux panes:

```bash
cd ~/Indigo-edits && set -a && . ~/Indigo/.env && set +a && . .venv/bin/activate && export MRT_PORT=8002
claude "$(cat ~/Indigo/prompts/edits-retry-claude.md)"

cd ~/Indigo-eval && set -a && . ~/Indigo/.env && set +a && . .venv/bin/activate && export MRT_PORT=8003
claude "$(cat ~/Indigo/prompts/dedupe-eval-claude.md)"
```

Put `eval/gold/record_1.json` (coming from me) into `~/Indigo-eval/eval/gold/` as soon as you have it.

The main Claude session is free during Phase 2: give it the README (Appendix D), the second demo fixture (`samples/record_1_part.pdf`, pages 5 to 40 of the sample, see below), and the full 188-page run if it has not finished.

```python
import fitz; d = fitz.open("samples/record_1.pdf"); d.select(list(range(4, 40))); d.save("samples/record_1_part.pdf")
```

## 7. About 15:45, merge Phase 2

```bash
cd ~/Indigo && git merge --no-ff edits-retry -m "merge: edits-retry"
cd ../Indigo-eval && git rebase main        # resolve conflicts here, they are in api.py and app.js
cd ~/Indigo && git merge --no-ff dedupe-eval -m "merge: dedupe-eval"
pytest && python -m app                      # smoke test on the merged main
```

## 8. 16:00 to 16:20, freeze

```bash
rm -rf /tmp/clean && git clone ~/Indigo /tmp/clean && cd /tmp/clean
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp ~/Indigo/.env . && python -m app          # drop samples/record_1.pdf in the browser
```

Fix only what this run reveals. Push at 16:20. Rehearse the demo script from the README once.

## Timeline for the demo (what to say)

- Built: timeline across bundles, vision extraction per page, rule-based boundaries with reasons, persisted edits, resume after failure, duplicates, eval against gold labels.
- Cut: auth, multi-patient, cloud, React, OCR, polish.
- Decided alone: the README "Assumptions" section, read it out.
