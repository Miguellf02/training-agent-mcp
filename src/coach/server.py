"""The MCP server: two read-only tools and one resource. Nothing else.

- get_activities   (tool)     -> individual runs in a date range
- get_weekly_load  (tool)     -> weekly totals + trend, computed in code
- coach://goal     (resource) -> the target race; static data, so a resource, not a tool
- coach://goal/{as_of} (resource template) -> the same, with days_to_race counted from as_of
"""

import datetime as dt
from typing import Annotated

import logfire
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from coach.analytics import goal_status, history_weeks, normalise, parse_iso_week, weekly_load
from coach.config import Settings
from coach.models import ActivitiesResult, GoalConfig, LoadPolicy, WeeklyLoadResult
from coach.observability import configure_observability
from coach.sources import (
    ActivitySource,
    FetchResult,
    FixtureSource,
    IntervalsSource,
    SourceError,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
GOAL_FIELDS = "race, race_date (YYYY-MM-DD) and distance_km"

INSTRUCTIONS = """\
Read-only access to one runner's training data (running only).
All numbers are pre-computed: never re-add distances or recompute loads yourself.
Heart rate may be missing; say so instead of estimating it.
Use get_weekly_load for "how was my week"; use get_activities only to inspect specific runs.
Some activities may be blocked (exist but unreadable): report them, never treat them as rest.
"""


def _traced_fetch(source: ActivitySource, start: dt.date, end: dt.date) -> FetchResult:
    """Time the data source. Records how much came back, never what. Errors become ToolError."""
    failure: SourceError
    with logfire.span("source fetch", source=type(source).__name__) as span:
        try:
            fetched = source.fetch(start, end)
        except SourceError as exc:
            failure = exc
            span.set_level("error")
            span.set_attribute("coach.error_type", type(exc).__name__)
        else:
            span.set_attribute("coach.runs", len(fetched.runs))
            span.set_attribute("coach.blocked", len(fetched.blocked))
            return fetched
    raise ToolError(str(failure)) from failure


def build_server(settings: Settings, source: ActivitySource, today: dt.date) -> MCPServer:
    mcp = MCPServer(name="coach", instructions=INSTRUCTIONS)
    threshold = settings.threshold_pace_sec_per_km
    policy = LoadPolicy(
        spike_threshold_pct=settings.spike_threshold_pct,
        layoff_min_zero_weeks=settings.layoff_min_zero_weeks,
        max_weekly_increase_pct=settings.max_weekly_increase_pct,
        trend_window_weeks=settings.trend_window_weeks,
        streak_lookback_weeks=settings.streak_lookback_weeks,
        incomplete_data_max_km=settings.incomplete_data_max_km,
    )

    @mcp.tool(annotations=READ_ONLY)
    def get_activities(
        start: Annotated[dt.date, Field(description="First day, inclusive (YYYY-MM-DD).")],
        end: Annotated[dt.date, Field(description="Last day, inclusive (YYYY-MM-DD).")],
    ) -> ActivitiesResult:
        """List individual runs between two dates: distance (km), time (min), pace (m:ss/km),
        heart rate (often null) and estimated load. Use it to inspect specific sessions;
        for weekly totals or trends use get_weekly_load instead. Max range: 31 days."""
        if end < start:
            raise ToolError(f"'end' ({end}) is before 'start' ({start}). Swap them.")
        span = (end - start).days + 1
        if span > settings.max_range_days:
            raise ToolError(
                f"Range is {span} days; max is {settings.max_range_days}. "
                "Narrow it, or use get_weekly_load for longer trends."
            )
        fetched = _traced_fetch(source, start, end)
        runs = sorted((normalise(r, threshold) for r in fetched.runs), key=lambda a: a.date)
        return ActivitiesResult(
            start=start,
            end=end,
            count=len(runs),
            activities=runs,
            missing_heart_rate=sum(a.avg_heart_rate_bpm is None for a in runs),
            blocked=sorted(fetched.blocked, key=lambda b: b.date),
        )

    @mcp.tool(annotations=READ_ONLY)
    def get_weekly_load(
        week: Annotated[
            str, Field(description="Target ISO week, e.g. '2026-W42' (weeks run Mon-Sun).")
        ],
        n_weeks: Annotated[
            int,
            Field(ge=1, le=12, description="How many weeks to return, ending at the target."),
        ] = 4,
    ) -> WeeklyLoadResult:
        """Weekly running totals (km, hours, runs, longest run, estimated load) for the target
        week and the weeks before it, oldest first. Also returns the % change vs the previous
        weeks' average and how many consecutive 0 km weeks end at the target week.
        Weeks with blocked (unreadable) activities undercount and set data_complete=false.
        Also returns deterministic flags (load_spike, layoff, incomplete_data, missing_hr),
        a ceiling for next week's km and next_week_start (the Monday of the planned week).
        This is the main tool for 'how was my week' and for planning the next one."""
        try:
            target_monday = parse_iso_week(week)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        first_monday = target_monday - dt.timedelta(weeks=history_weeks(n_weeks, policy) - 1)
        fetched = _traced_fetch(source, first_monday, target_monday + dt.timedelta(days=6))
        runs = [normalise(r, threshold) for r in fetched.runs]
        return weekly_load(runs, fetched.blocked, target_monday, n_weeks, policy)

    def load_goal(as_of: dt.date) -> str:
        path = settings.goal_path
        try:
            config = GoalConfig.model_validate_json(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ResourceError(
                f"Cannot read the goal file {path}. Create it with {GOAL_FIELDS}, "
                "or point COACH_GOAL_PATH to it."
            ) from exc
        except ValidationError as exc:
            raise ResourceError(
                f"Invalid goal file {path}: {exc.errors()[0]['msg']}. It must be JSON with "
                f"{GOAL_FIELDS}."
            ) from exc
        return goal_status(config, as_of).model_dump_json()

    @mcp.resource("coach://goal", mime_type="application/json")
    def goal() -> str:
        """The target race (name, date, distance, target time) and days left from today."""
        return load_goal(today)

    @mcp.resource("coach://goal/{as_of}", mime_type="application/json")
    def goal_as_of(as_of: str) -> str:
        """The target race with days_to_race counted from as_of (YYYY-MM-DD). Use the
        next_week_start from get_weekly_load to count from the Monday of the planned week."""
        try:
            day = dt.date.fromisoformat(as_of)
        except ValueError as exc:
            raise ResourceError(
                f"Invalid as_of {as_of!r}; use YYYY-MM-DD, e.g. coach://goal/2026-10-19."
            ) from exc
        return load_goal(day)

    return mcp


def make_source(settings: Settings) -> ActivitySource:
    if settings.fixtures_path is not None:
        return FixtureSource(settings.fixtures_path)
    if settings.intervals_api_key is None:
        raise SystemExit(
            "Set COACH_INTERVALS_API_KEY for live data, or COACH_FIXTURES_PATH for offline mode."
        )
    return IntervalsSource(
        settings.intervals_api_key.get_secret_value(), settings.intervals_athlete_id
    )


def main() -> None:
    settings = Settings()
    configure_observability(settings, "coach-mcp")
    build_server(settings, make_source(settings), dt.date.today()).run("stdio")


if __name__ == "__main__":
    main()
