"""Tests run fully offline against tests/fixtures/ (invented data)."""

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from coach.analytics import estimate_load, format_pace, parse_iso_week, weekly_load
from coach.config import Settings
from coach.models import Activity, BlockedActivity, LoadPolicy
from coach.server import build_server
from coach.sources import FixtureSource, _parse

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 11, 6)
POLICY = LoadPolicy(
    spike_threshold_pct=30,
    layoff_min_zero_weeks=2,
    max_weekly_increase_pct=10,
    trend_window_weeks=4,
    streak_lookback_weeks=12,
    incomplete_data_max_km=10,
)


@pytest.fixture
def server() -> Any:
    settings = Settings(
        fixtures_path=ROOT / "tests/fixtures/activities.json",
        goal_path=ROOT / "tests/fixtures/goal.json",
        _env_file=None,  # type: ignore[call-arg]
    )
    return build_server(settings, FixtureSource(settings.fixtures_path), TODAY)  # type: ignore[arg-type]


async def call(server: Any, tool: str, **args: Any) -> dict[str, Any]:
    result = await server.call_tool(tool, args)
    structured = result.structured_content
    assert structured is not None
    return dict(structured)


# --- pure functions ---------------------------------------------------------


def test_one_hour_at_threshold_is_100() -> None:
    assert estimate_load(distance_km=3600 / 310, moving_time_s=3600, threshold_pace_s=310) == 100.0


def test_format_pace() -> None:
    assert format_pace(318.5) == "5:18"


def test_bad_iso_week_raises() -> None:
    with pytest.raises(ValueError):
        parse_iso_week("2026-42")


def test_strava_stub_is_blocked_not_fatal() -> None:
    records = [
        {
            "id": "1",
            "start_date_local": "2026-10-13T00:00:00",
            "type": "Run",
            "distance": 5000,
            "moving_time": 1500,
        },
        {
            "id": "2",
            "start_date_local": "2026-10-17T21:03:12",
            "source": "STRAVA",
            "_note": "STRAVA activities are not available via the API",
        },
    ]
    result = _parse(records)
    assert [r.id for r in result.runs] == ["1"]
    assert [(b.id, b.date) for b in result.blocked] == [("2", dt.date(2026, 10, 17))]
    assert "import the Strava archive" in result.blocked[0].reason


def test_week_with_only_a_blocked_activity_is_not_rest() -> None:
    # W41 has a readable run; W42 (the target) only has a blocked, unreadable one.
    prior_run = Activity(
        id="1",
        date=dt.date(2026, 10, 7),
        name="Easy",
        distance_km=8.0,
        moving_time_min=44.0,
        pace_min_per_km="5:30",
        est_load=50.0,
    )
    blocked = [BlockedActivity(id="2", date=dt.date(2026, 10, 17), reason="Strava")]
    out = weekly_load([prior_run], blocked, dt.date(2026, 10, 12), n_weeks=2, policy=POLICY)
    assert out.weeks[-1].blocked_activities == 1
    assert out.data_complete is False
    assert out.zero_weeks_streak == 0
    assert out.change_vs_prior_avg_pct is None  # would otherwise be a fake -100 %
    assert out.flags == ["incomplete_data"]
    assert out.suggested_max_next_week_km is None  # an undercounted base gives no ceiling
    assert out.fallback_max_next_week_km == 10  # but the plan still has a limit from code


# --- the four eval weeks, at tool level ------------------------------------


@pytest.mark.anyio
async def test_race_week_is_a_spike(server: Any) -> None:
    out = await call(server, "get_weekly_load", week="2026-W42", n_weeks=4)
    target = out["weeks"][-1]
    assert target["distance_km"] == 23.0
    assert target["longest_run_km"] == 20.0
    assert out["change_vs_prior_avg_pct"] is not None
    assert out["change_vs_prior_avg_pct"] > 300  # 23 km vs ~4.3 km/week before
    assert out["flags"] == ["load_spike", "missing_hr"]
    # After a spike: recent average (10 + 3 + 0 + 23) / 4, no increase.
    assert out["suggested_max_next_week_km"] == 9.0
    assert out["fallback_max_next_week_km"] is None  # complete data: no fallback
    assert out["next_week_start"] == "2026-10-19"


@pytest.mark.anyio
async def test_layoff_counts_zero_weeks(server: Any) -> None:
    out = await call(server, "get_weekly_load", week="2026-W45", n_weeks=4)
    assert out["zero_weeks_streak"] == 3
    assert out["weeks"][0]["week"] == "2026-W42"
    assert out["flags"] == ["layoff"]  # no runs, so no missing_hr either
    assert out["suggested_max_next_week_km"] == 5.8  # (23 + 0 + 0 + 0) / 4, no increase


@pytest.mark.anyio
@pytest.mark.parametrize("week", ["2026-W38", "2026-W42", "2026-W45"])
async def test_trend_does_not_depend_on_n_weeks(server: Any, week: str) -> None:
    # Regression: a planner asking for 8 weeks once got a different ceiling than the analyst.
    short = await call(server, "get_weekly_load", week=week, n_weeks=1)
    long = await call(server, "get_weekly_load", week=week, n_weeks=8)
    keys = (
        "change_vs_prior_avg_pct",
        "data_complete",
        "flags",
        "suggested_max_next_week_km",
        "zero_weeks_streak",
    )
    assert {k: short[k] for k in keys} == {k: long[k] for k in keys}
    assert len(short["weeks"]) == 1 and len(long["weeks"]) == 8


@pytest.mark.anyio
@pytest.mark.parametrize("n_weeks", [1, 4, 12])
async def test_zero_weeks_streak_does_not_depend_on_n_weeks(tmp_path: Path, n_weeks: int) -> None:
    # Last run in W36, target W42: W37 to W42 are six empty weeks, however many are listed.
    fixture = tmp_path / "activities.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "id": "1",
                    "start_date_local": "2026-08-31T00:00:00",
                    "type": "Run",
                    "distance": 8000,
                    "moving_time": 2640,
                }
            ]
        ),
        encoding="utf-8",
    )
    settings = Settings(
        fixtures_path=fixture,
        goal_path=ROOT / "tests/fixtures/goal.json",
        _env_file=None,  # type: ignore[call-arg]
    )
    server = build_server(settings, FixtureSource(fixture), TODAY)
    out = await call(server, "get_weekly_load", week="2026-W42", n_weeks=n_weeks)
    assert out["zero_weeks_streak"] == 6


@pytest.mark.anyio
async def test_week_after_empty_weeks_keeps_its_own_base(server: Any) -> None:
    # W38: 9 km after three empty weeks. The recent average (2.25) would cut the runner
    # below the week they just ran, so the base is max(average, target week) = 9.
    out = await call(server, "get_weekly_load", week="2026-W38", n_weeks=4)
    assert out["flags"] == ["missing_hr"]
    assert out["change_vs_prior_avg_pct"] is None
    assert out["suggested_max_next_week_km"] == 9.9  # 9 x 1.10


@pytest.mark.anyio
async def test_activities_flag_missing_heart_rate(server: Any) -> None:
    out = await call(server, "get_activities", start="2026-10-12", end="2026-10-18")
    assert out["count"] == 2
    assert out["missing_heart_rate"] == 2
    assert out["activities"][1]["pace_min_per_km"] == "5:00"


@pytest.mark.anyio
async def test_range_too_long_is_rejected(server: Any) -> None:
    # In-process the error is raised; over MCP the protocol returns it as isError=True.
    with pytest.raises(ToolError, match="max is 31"):
        await server.call_tool("get_activities", {"start": "2026-01-01", "end": "2026-06-01"})


@pytest.mark.anyio
async def test_goal_resource_computes_days_to_race(server: Any) -> None:
    contents = await server.read_resource("coach://goal")
    goal = json.loads(contents[0].content)
    assert goal["days_to_race"] == (dt.date(2027, 5, 9) - TODAY).days


@pytest.mark.anyio
async def test_goal_counts_days_from_the_planned_week(server: Any) -> None:
    contents = await server.read_resource("coach://goal/2026-10-19")
    goal = json.loads(contents[0].content)
    assert goal["as_of"] == "2026-10-19"
    assert goal["days_to_race"] == 202  # 2026-10-19 -> 2027-05-09, not counted from today
    with pytest.raises(ResourceError, match="use YYYY-MM-DD"):
        await server.read_resource("coach://goal/next-monday")


def test_analytics_does_not_load_the_http_client() -> None:
    # A fresh interpreter: analytics is pure, so importing it must not pull in httpx.
    code = "import sys, coach.analytics; sys.exit('httpx' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
