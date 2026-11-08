"""The goal resources when goal.json is missing or wrong."""

import datetime as dt
from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError

from coach.analytics import goal_status
from coach.config import Settings
from coach.models import GoalConfig
from coach.server import build_server
from coach.sources import FixtureSource

TODAY = dt.date(2026, 11, 6)
URIS = ["coach://goal", "coach://goal/2026-10-19"]


def server_with_goal(goal_path: Path) -> MCPServer:
    settings = Settings(goal_path=goal_path, _env_file=None)  # type: ignore[call-arg]
    return build_server(settings, FixtureSource(goal_path), TODAY)


def test_goal_status_counts_days_from_as_of() -> None:
    config = GoalConfig(race="Test race", race_date=dt.date(2027, 1, 11), distance_km=10.0)
    status = goal_status(config, dt.date(2027, 1, 1))
    assert status.days_to_race == 10
    assert status.as_of == dt.date(2027, 1, 1)
    assert status.race == "Test race"


@pytest.mark.anyio
@pytest.mark.parametrize("uri", URIS)
async def test_missing_goal_file_says_how_to_fix_it(tmp_path: Path, uri: str) -> None:
    server = server_with_goal(tmp_path / "nope.json")
    with pytest.raises(ResourceError, match="COACH_GOAL_PATH"):
        await server.read_resource(uri)


@pytest.mark.anyio
@pytest.mark.parametrize("uri", URIS)
@pytest.mark.parametrize(
    "content",
    ["{bad", '{"race": "No date"}', '{"race": "x", "race_date": "soon", "distance_km": 10}'],
    ids=["invalid-json", "missing-fields", "bad-date"],
)
async def test_invalid_goal_file_says_how_to_fix_it(tmp_path: Path, uri: str, content: str) -> None:
    path = tmp_path / "goal.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ResourceError, match="race_date"):
        await server_with_goal(path).read_resource(uri)
