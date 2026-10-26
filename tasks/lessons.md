# Lessons

- 2026-10-26: Planned a "compare against live Firecrawl API with FIRECRAWL_API_KEY" step. The project
  uses no API keys and no outside API calls (CLAUDE.md: the office runs skills + MCP only). Before planning
  any step that calls an outside service, check CLAUDE.md's environment notes; verify against local
  references instead (e.g. build the open-source crate Firecrawl uses and run it locally).
- 2026-10-26: Asked grilling questions as numbered text; the user wanted them as question cards. When putting
  decisions to the user, use the AskUserQuestion card (up to 4 per card, recommended option first), not prose.
