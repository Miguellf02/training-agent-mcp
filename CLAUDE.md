# coach — development rules

- Python 3.12, Pydantic V2, `mypy --strict` must pass. No `Any` in `src/`.
- All arithmetic lives in `src/coach/analytics.py` as pure, tested functions.
- The MCP server is read-only: no tool may write, delete or call a non-GET endpoint.
- New tools need a strong reason. Prefer a new parameter on an existing tool.
- Tool errors raise `ToolError` with a message that tells the agent how to fix the call.
- Before finishing a change: `uv run ruff check . && uv run mypy src && uv run pytest`.
