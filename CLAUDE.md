@AGENTS.md

# Claude Code Project Guidance

- Use `docs/PROJECT_ARCHITECTURE.md` for project context; do not infer architecture
  from filenames alone.
- Before editing, inspect the affected implementation, its tests, and both sides
  of any API contract.
- Prefer a small, complete change over speculative abstractions.
- Use repository commands exactly as documented. Do not replace `uv`/`npm` or add
  dependencies unless the task requires it.
- When handing off, state changed behavior, files affected, checks actually run,
  and any remaining operational or migration step.
