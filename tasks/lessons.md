# Lessons

- 2026-10-26: Planned a "compare against live Firecrawl API with FIRECRAWL_API_KEY" step. The project
  uses no API keys and no outside API calls (CLAUDE.md: the office runs skills + MCP only). Before planning
  any step that calls an outside service, check CLAUDE.md's environment notes; verify against local
  references instead (e.g. build the open-source crate Firecrawl uses and run it locally).
- 2026-10-26: Asked grilling questions as numbered text; the user wanted them as question cards. When putting
  decisions to the user, use the AskUserQuestion card (up to 4 per card, recommended option first), not prose.
- 2026-10-26: Recommended "hard text pages through the vision step" as next work from CLAUDE.md's open-work list;
  it had already shipped in PR #6 from another session (the list was stale). Before recommending or planning
  work, check main's git log and code for it (`git log --oneline origin/main | grep -i <topic>`), not just
  the open-work list; fix the list when it's stale.
- 2026-10-27: Planned .msg support from scratch; PR #13 (msg2md.py, .msg + .eml) was already open from another
  session. Before planning a feature, also check open PRs and branches: `gh pr list --state all | grep -i <topic>`.
