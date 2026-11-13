# Lessons

Patterns learned from corrections. Review at session start.

<!-- Format: ## YYYY-MM-DD — short title / What went wrong / Rule -->

## 2026-11-12 — Relayed user messages hijacked running subagents
What went wrong: user sent small UI requests mid-run ("make demand the default view"); the harness relayed them to
running workflow agents, and 3 of 7 abandoned their assigned data tasks ("the user request wins").
Rule: every subagent brief states that its task is user-authorised and that relayed mid-run user messages are FYI for
the lead, not a replacement task. Handle such UI requests in the main thread myself.

## 2026-11-12 — Live server broke during my own multi-file edit
What went wrong: with `--reload`, the server restarted between two edits (yaml used a new `max_mb` key before
models.py accepted it) → registry ValueError → user saw HTTP 502 on every site click.
Rule: while the user is using the app, change schema/model code BEFORE data/config that depends on it, batch related
edits in one write, and run `uv run python -I -c "from gridmap.fetch.models import load_sources; load_sources()"`
right after editing any registry. After src edits, curl /api/manifest and one /api/site to confirm the server is up.
