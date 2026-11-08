"""The agent runner with a fake LLM and the real MCP server, on synthetic data: no API calls."""

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.profiles import ModelProfile

from coach.agents.contracts import AnalystReport, PlanDay, WeekPlan
from coach.agents.runner import (
    RunnerError,
    build_model,
    load_agent_spec,
    planner_instructions,
    run_week,
)
from coach.config import Settings

ROOT = Path(__file__).resolve().parents[1]
WEEK = "2026-W30"
WEEKLY_LOAD = "mcp__coach__get_weekly_load"
ACTIVITIES = "mcp__coach__get_activities"
RESOURCE = "ReadMcpResourceTool"

ModelFunction = Callable[[list[ModelMessage], AgentInfo], ModelResponse]


def fake_model(function: ModelFunction) -> FunctionModel:
    return FunctionModel(function, profile=ModelProfile(supports_json_schema_output=True))


def tool_returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def analyst_json(load: dict[str, object] | None = None, flags: list[str] | None = None) -> str:
    source = load if load is not None else {}
    return json.dumps(
        {
            "week": WEEK,
            "distance_km": 35.0,
            "runs": 4,
            "longest_run_km": 14.0,
            "change_vs_prior_avg_pct": source.get("change_vs_prior_avg_pct", 10.5),
            "zero_weeks_streak": 0,
            "data_complete": True,
            "blocked_count": 0,
            "flags": flags if flags is not None else [],
            "inferred_flags": [],
            "suggested_max_next_week_km": source.get("suggested_max_next_week_km", 38.5),
            "fallback_max_next_week_km": None,
            "next_week_start": source.get("next_week_start", "2026-07-27"),
            "notes": "A normal week.",
        }
    )


def plan_json() -> str:
    sessions = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return json.dumps(
        {
            "question_for_runner": None,
            "days": [{"day": d, "session": "Easy", "approx_km": 5} for d in sessions],
            "reasoning": "Under the ceiling.",
        }
    )


def analyst_function(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    returns = tool_returns(messages)
    if not returns:
        return ModelResponse(parts=[ToolCallPart(WEEKLY_LOAD, {"week": WEEK, "n_weeks": 4})])
    content = returns[0].content
    assert isinstance(content, dict)
    return ModelResponse(parts=[TextPart(analyst_json(content))])


def planner_function(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    if not tool_returns(messages):
        args = {"server": "coach", "uri": "coach://goal/2026-07-27"}
        return ModelResponse(parts=[ToolCallPart(RESOURCE, args)])
    return ModelResponse(parts=[TextPart(plan_json())])


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    goal = tmp_path / "goal.json"
    goal.write_text(
        json.dumps({"race": "Test race", "race_date": "2026-08-30", "distance_km": 10}),
        encoding="utf-8",
    )
    return Settings(
        fixtures_path=ROOT / "evals/synthetic/build_week.json",
        goal_path=goal,
        _env_file=None,  # type: ignore[call-arg]
    )


# --- the whole run ---------------------------------------------------------


@pytest.mark.anyio
async def test_run_week_passes_server_numbers_to_a_validated_report(settings: Settings) -> None:
    models = {"analyst": fake_model(analyst_function), "planner": fake_model(planner_function)}
    report = await run_week(WEEK, settings, models)
    # These come from the real server reading the synthetic fixture, through the tool call.
    assert report.analyst.change_vs_prior_avg_pct == 10.5
    assert report.analyst.suggested_max_next_week_km == 38.5
    assert str(report.analyst.next_week_start) == "2026-07-27"
    assert len(report.plan.days) == 7


@pytest.mark.anyio
async def test_each_agent_gets_only_its_allowlisted_tools(settings: Settings) -> None:
    seen: dict[str, set[str]] = {}

    def analyst(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["analyst"] = {t.name for t in info.function_tools}
        return analyst_function(messages, info)

    def planner(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["planner"] = {t.name for t in info.function_tools}
        return planner_function(messages, info)

    await run_week(WEEK, settings, {"analyst": fake_model(analyst), "planner": fake_model(planner)})
    assert seen["analyst"] == {WEEKLY_LOAD, ACTIVITIES}
    assert seen["planner"] == {WEEKLY_LOAD, RESOURCE}


@pytest.mark.anyio
async def test_planner_reads_the_goal_counted_from_the_planned_week(settings: Settings) -> None:
    goals: list[object] = []

    def planner(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        goals.extend(part.content for part in tool_returns(messages))
        return planner_function(messages, info)

    models = {"analyst": fake_model(analyst_function), "planner": fake_model(planner)}
    await run_week(WEEK, settings, models)
    goal = json.loads(str(goals[0]))
    assert goal["race"] == "Test race"
    assert goal["days_to_race"] == 34  # 2026-07-27 -> 2026-08-30, computed by the server


@pytest.mark.anyio
async def test_unreadable_resource_lets_the_model_fix_the_call(settings: Settings) -> None:
    errors: list[str] = []

    def planner(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        returned = tool_returns(messages)
        if returned:
            errors.append(str(returned[0].content))
            return ModelResponse(parts=[TextPart(plan_json())])
        args = {"server": "coach", "uri": "coach://goal/next-monday"}
        return ModelResponse(parts=[ToolCallPart(RESOURCE, args)])

    models = {"analyst": fake_model(analyst_function), "planner": fake_model(planner)}
    report = await run_week(WEEK, settings, models)
    assert "YYYY-MM-DD" in errors[0]  # the server's own message reaches the model
    assert len(report.plan.days) == 7


# --- validation of the model's output --------------------------------------


@pytest.mark.anyio
async def test_invalid_output_gets_one_retry(settings: Settings) -> None:
    calls: list[int] = []

    def analyst(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        calls.append(len(messages))
        flags = ["invented_flag"] if len(calls) == 1 else []
        return ModelResponse(parts=[TextPart(analyst_json(flags=flags))])

    models = {"analyst": fake_model(analyst), "planner": fake_model(planner_function)}
    report = await run_week(WEEK, settings, models)
    assert len(calls) == 2
    assert report.analyst.flags == []


@pytest.mark.anyio
async def test_output_that_stays_invalid_raises_a_runner_error(settings: Settings) -> None:
    def analyst(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("Here is your summary!")])

    models = {"analyst": fake_model(analyst), "planner": fake_model(planner_function)}
    with pytest.raises(RunnerError, match="analyst run failed"):
        await run_week(WEEK, settings, models)


@pytest.mark.anyio
async def test_analyst_failure_stops_before_the_planner(settings: Settings) -> None:
    planner_calls: list[int] = []

    def analyst(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        failed = json.loads(analyst_json())
        failed.update(next_week_start=None, notes="The tool failed.")
        return ModelResponse(parts=[TextPart(json.dumps(failed))])

    def planner(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        planner_calls.append(1)
        return planner_function(messages, info)

    models = {"analyst": fake_model(analyst), "planner": fake_model(planner)}
    with pytest.raises(RunnerError, match="The tool failed"):
        await run_week(WEEK, settings, models)
    assert planner_calls == []


# --- contracts -------------------------------------------------------------


def test_plan_must_have_seven_days_in_order() -> None:
    monday = PlanDay(day="Mon", session="Rest", approx_km=0)
    with pytest.raises(ValidationError, match="exactly 7 entries"):
        WeekPlan(question_for_runner=None, days=[monday], reasoning="Too short.")


def test_analyst_rejects_a_flag_the_server_does_not_define() -> None:
    with pytest.raises(ValidationError):
        AnalystReport.model_validate_json(analyst_json(flags=["invented_flag"]))


def test_plan_rejects_negative_km() -> None:
    with pytest.raises(ValidationError):
        PlanDay(day="Mon", session="Rest", approx_km=-1)


# --- agent files and configuration -----------------------------------------


def test_agent_files_define_models_and_tools() -> None:
    analyst, planner = load_agent_spec("analyst"), load_agent_spec("planner")
    assert (analyst.model, planner.model) == ("haiku", "sonnet")
    assert analyst.tools == {WEEKLY_LOAD, ACTIVITIES}
    assert planner.tools == {WEEKLY_LOAD, RESOURCE}


def test_planner_keeps_its_rules_but_not_the_markdown_output() -> None:
    instructions = planner_instructions(load_agent_spec("planner"))
    assert "Volume ceiling" in instructions
    assert "question_for_runner" in instructions
    assert "Return markdown only" not in instructions


def test_missing_api_key_says_how_to_fix_it(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    without_key = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(RunnerError, match="ANTHROPIC_API_KEY"):
        build_model("haiku", without_key)


@pytest.mark.anyio
async def test_invalid_week_never_starts_an_agent(settings: Settings) -> None:
    with pytest.raises(RunnerError, match="Invalid ISO week"):
        await run_week("2026-30", settings, {})


@pytest.mark.anyio
async def test_mcp_server_processes_stop_when_the_run_ends(settings: Settings) -> None:
    models = {"analyst": fake_model(analyst_function), "planner": fake_model(planner_function)}
    await run_week(WEEK, settings, models)
    children = subprocess.run(
        ["pgrep", "-P", str(os.getpid()), "-f", "coach.server"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert children.stdout.split() == []
