"""PostToolUse hook (Edit|Write): lint and type-check after any .py edit.

Exit 0 when the file is not Python or both checks pass. Exit 2 with the tool output on stderr
when a check fails, so Claude sees the error and fixes it in its next step.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

CHECKS: tuple[tuple[str, ...], ...] = (
    ("uv", "run", "ruff", "check"),
    ("uv", "run", "mypy", "src"),
)


def edited_file(payload: object) -> str | None:
    """The file_path from the hook's tool_input, or None if it is not there."""
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    path = tool_input.get("file_path")
    return path if isinstance(path, str) else None


def main() -> int:
    path = edited_file(json.load(sys.stdin))
    if path is None or not path.endswith(".py"):
        return 0
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    failures: list[str] = []
    for cmd in CHECKS:
        proc = subprocess.run(cmd, cwd=project, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            failures.append(f"$ {' '.join(cmd)}\n{proc.stdout}{proc.stderr}".strip())
    if not failures:
        return 0
    print(
        f"Checks failed after editing {path}. Fix these errors:\n\n" + "\n\n".join(failures),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
