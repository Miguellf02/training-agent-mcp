"""PreToolUse hook (mcp__coach__get_activities): client policy, max 14 days per call.

The server accepts up to 31 days (a valid request). This stricter client-side limit keeps
large activity lists out of the agent's context. Exit 2 blocks the call and the stderr
message tells the agent how to fix it. Malformed dates pass through: the server's own
validation returns the actionable error for those.
"""

import datetime as dt
import json
import sys

MAX_DAYS = 14


def requested_span_days(payload: object) -> int | None:
    """Inclusive day span of the requested range, or None if it cannot be read."""
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    start, end = tool_input.get("start"), tool_input.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        return (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days + 1
    except ValueError:
        return None


def main() -> int:
    span = requested_span_days(json.load(sys.stdin))
    if span is None or span <= MAX_DAYS:
        return 0
    print(
        f"Blocked by client policy: get_activities range is {span} days; max is {MAX_DAYS}. "
        "Split it into calls of 14 days or less (e.g. one per ISO week), "
        "or use get_weekly_load for totals and trends.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
