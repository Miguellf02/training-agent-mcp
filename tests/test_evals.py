"""The eval harness itself. An eval that cannot fail, or fails for the wrong reason, misleads."""

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from test_agents import fake_model

from coach.agents.contracts import AnalystReport, RunMetrics, WeekPlan, WeekReport
from coach.analytics import pass_rate_pct, plan_total_km
from coach.config import Settings
from coach.evals import (
    CHECKABLE_FIELDS,
    AttemptResult,
    EvalCase,
    EvalSetupError,
    EvalSuite,
    FieldEquals,
    FieldNotNull,
    JudgeCriterion,
    LongestDayAtLeast,
    LongestDayAtMost,
    PlanTotalAtLeast,
    PlanTotalAtMost,
    check_criterion,
    dry_run_case,
    format_table,
    load_suite,
    main,
    progress_line,
    run_attempt,
    save_results,
    summarise,
)

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
ROOT = Path(__file__).resolve().parents[1]

NORMAL_ANALYST = {
    "week": "2026-W30",
    "distance_km": 35.0,
    "runs": 4,
    "longest_run_km": 14.0,
    "change_vs_prior_avg_pct": 10.5,
    "zero_weeks_streak": 0,
    "data_complete": True,
    "blocked_count": 0,
    "flags": [],
    "inferred_flags": [],
    "suggested_max_next_week_km": 38.5,
    "fallback_max_next_week_km": None,
    "next_week_start": "2026-07-27",
    "notes": "A normal week.",
}
LAYOFF_ANALYST = {
    **NORMAL_ANALYST,
    "week": "2026-W20",
    "distance_km": 0.0,
    "runs": 0,
    "longest_run_km": 0.0,
    "change_vs_prior_avg_pct": -100.0,
    "zero_weeks_streak": 3,
    "flags": ["layoff"],
    "suggested_max_next_week_km": 6.0,
    "next_week_start": "2026-05-18",
}


def plan(kms: list[float], question: str | None = None) -> dict[str, object]:
    return {
        "question_for_runner": question,
        "days": [
            {"day": d, "session": "Easy", "approx_km": km}
            for d, km in zip(DAYS, kms, strict=True)
        ],
        "reasoning": "Under the ceiling.",
    }


GOOD_NORMAL_PLAN = plan([0, 7, 0, 7, 0, 7, 15])
GOOD_LAYOFF_PLAN = plan([0, 2, 0, 2, 0, 2, 0], "Why did you stop running?")


def answers(payload: dict[str, object]) -> FunctionModel:
    def respond(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(json.dumps(payload))])

    return fake_model(respond)


def report(analyst: dict[str, object], week_plan: dict[str, object]) -> WeekReport:
    return WeekReport(
        analyst=AnalystReport.model_validate(analyst),
        plan=WeekPlan.model_validate(week_plan),
        metrics=RunMetrics(agents=[], total_cost_usd=None),
    )


def case(case_id: str) -> EvalCase:
    return next(c for c in load_suite().cases if c.id == case_id)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(goal_path=tmp_path / "unused.json", _env_file=None)  # type: ignore[call-arg]


# --- the cases in the repo -------------------------------------------------


def test_repo_cases_load_and_include_both_directions() -> None:
    ids = {c.id for c in load_suite().cases}
    assert {"normal-build-week", "three-week-layoff"} <= ids


@pytest.mark.anyio
async def test_expected_values_in_every_repo_case_match_the_server(settings: Settings) -> None:
    for repo_case in load_suite().cases:
        assert await dry_run_case(repo_case, settings) == [], repo_case.id


def test_repo_cases_use_only_synthetic_goals_and_ids() -> None:
    for repo_case in load_suite().cases:
        for activity in repo_case.input.activities:
            assert str(activity["id"]).startswith("syn-"), repo_case.id
    assert all(c.input.goal.race.startswith("Synthetic") for c in load_suite().cases)


@pytest.mark.parametrize(
    ("case_id", "analyst", "good_plan"),
    [
        ("normal-build-week", NORMAL_ANALYST, GOOD_NORMAL_PLAN),
        ("three-week-layoff", LAYOFF_ANALYST, GOOD_LAYOFF_PLAN),
    ],
)
def test_a_reference_answer_passes_every_deterministic_check(
    case_id: str, analyst: dict[str, object], good_plan: dict[str, object]
) -> None:
    # A case no correct answer can pass measures the case, not the agents.
    reference = report(analyst, good_plan)
    for criterion in case(case_id).criteria:
        if not isinstance(criterion, JudgeCriterion):
            result = check_criterion(criterion, reference)
            assert result.status == "pass", result


# --- schema ----------------------------------------------------------------


def suite_json(**case_overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "a",
        "category": "normal",
        "description": "d",
        "input": {
            "week": "2026-W30",
            "activities": [],
            "goal": {"race": "Synthetic", "race_date": "2026-10-25", "distance_km": 10},
        },
        "question": "q",
        "expected_behavior": "e",
        "criteria": [{"type": "plan_total_km_at_most", "km": 10}],
    }
    return {"cases": [{**base, **case_overrides}]}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"criteria": [{"type": "field_equals", "field": "plan.nope", "expected": 1}]},
            "unknown field",
        ),
        ({"criteria": [{"type": "grep", "text": "35"}]}, "does not match any of the expected tags"),
        ({"criteria": []}, "at least 1 item"),
        ({"category": "misc"}, "Input should be"),
        ({"extra_key": 1}, "Extra inputs"),
        (
            {
                "input": {
                    "week": "2026-30",
                    "activities": [],
                    "goal": {"race": "S", "race_date": "2026-10-25", "distance_km": 10},
                }
            },
            "Invalid ISO week",
        ),
    ],
    ids=["unknown-field", "text-check", "no-criteria", "bad-category", "extra-key", "bad-week"],
)
def test_schema_rejects_bad_cases(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        EvalSuite.model_validate(suite_json(**overrides))


def test_schema_rejects_repeated_ids() -> None:
    doubled = suite_json()
    cases = doubled["cases"]
    assert isinstance(cases, list)
    with pytest.raises(ValidationError, match="repeated case ids"):
        EvalSuite.model_validate({"cases": [cases[0], cases[0]]})


def test_every_analyst_and_plan_field_can_be_checked() -> None:
    assert "analyst.flags" in CHECKABLE_FIELDS
    assert "plan.question_for_runner" in CHECKABLE_FIELDS
    assert "metrics.total_cost_usd" not in CHECKABLE_FIELDS


def test_invalid_cases_file_names_the_problem(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(suite_json(category="misc")), encoding="utf-8")
    with pytest.raises(EvalSetupError, match=r"cases\.0\.category"):
        load_suite(path)
    with pytest.raises(EvalSetupError, match="Cannot read"):
        load_suite(tmp_path / "missing.json")


# --- deterministic checks fail when they should ----------------------------


@pytest.mark.parametrize(
    ("criterion", "expected_status"),
    [
        (FieldEquals(type="field_equals", field="analyst.flags", expected=[]), "pass"),
        (FieldEquals(type="field_equals", field="analyst.flags", expected=["layoff"]), "fail"),
        (FieldEquals(type="field_equals", field="plan.question_for_runner", expected=None), "pass"),
        (FieldEquals(type="field_equals", field="analyst.distance_km", expected=35), "pass"),
        (FieldEquals(type="field_equals", field="analyst.distance_km", expected=35.1), "fail"),
        (FieldNotNull(type="field_not_null", field="plan.question_for_runner"), "fail"),
        (FieldNotNull(type="field_not_null", field="analyst.next_week_start"), "pass"),
        (PlanTotalAtMost(type="plan_total_km_at_most", km=36), "pass"),
        (PlanTotalAtMost(type="plan_total_km_at_most", km=35.9), "fail"),
        (PlanTotalAtLeast(type="plan_total_km_at_least", km=36), "pass"),
        (PlanTotalAtLeast(type="plan_total_km_at_least", km=36.1), "fail"),
        (LongestDayAtLeast(type="longest_day_km_at_least", km=15), "pass"),
        (LongestDayAtLeast(type="longest_day_km_at_least", km=16), "fail"),
        (LongestDayAtMost(type="longest_day_km_at_most", km=15), "pass"),
        (LongestDayAtMost(type="longest_day_km_at_most", km=14), "fail"),
    ],
)
def test_check_criterion(criterion: FieldEquals, expected_status: str) -> None:
    good = report(NORMAL_ANALYST, GOOD_NORMAL_PLAN)  # 36 km, longest day 15 km
    assert check_criterion(criterion, good).status == expected_status


def test_a_failed_check_says_what_it_saw() -> None:
    criterion = PlanTotalAtMost(type="plan_total_km_at_most", km=30)
    assert "36" in check_criterion(criterion, report(NORMAL_ANALYST, GOOD_NORMAL_PLAN)).detail


# --- whole attempts, with fake models and the real MCP server --------------


@pytest.mark.anyio
async def test_a_good_run_passes(settings: Settings) -> None:
    models = {"analyst": answers(NORMAL_ANALYST), "planner": answers(GOOD_NORMAL_PLAN)}
    result = await run_attempt(case("normal-build-week"), 1, settings, models)
    assert result.status == "pass", result.checks
    assert result.report is not None
    assert result.error is None


@pytest.mark.anyio
async def test_a_plan_over_the_ceiling_fails(settings: Settings) -> None:
    models = {
        "analyst": answers(NORMAL_ANALYST),
        "planner": answers(plan([0, 10, 0, 10, 0, 10, 15])),
    }
    result = await run_attempt(case("normal-build-week"), 1, settings, models)
    assert result.status == "fail"
    failed = [c.criterion for c in result.checks if c.status == "fail"]
    assert failed == ["plan total <= 38.5 km"]


@pytest.mark.anyio
async def test_an_empty_plan_fails_the_lower_bound(settings: Settings) -> None:
    # Without a lower bound, doing nothing would pass "stays under the ceiling".
    models = {"analyst": answers(NORMAL_ANALYST), "planner": answers(plan([0] * 7))}
    result = await run_attempt(case("normal-build-week"), 1, settings, models)
    assert result.status == "fail"
    assert "plan total >= 30 km" in [c.criterion for c in result.checks if c.status == "fail"]


@pytest.mark.anyio
async def test_plumbing_failure_is_an_error_not_a_fail(settings: Settings) -> None:
    def broken(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("not json")])

    models = {"analyst": fake_model(broken), "planner": answers(GOOD_NORMAL_PLAN)}
    result = await run_attempt(case("normal-build-week"), 1, settings, models)
    assert result.status == "error"
    assert result.error is not None and "analyst run failed" in result.error
    assert result.checks == []


@pytest.mark.anyio
async def test_timeout_is_an_error_not_a_fail(settings: Settings) -> None:
    models = {"analyst": answers(NORMAL_ANALYST), "planner": answers(GOOD_NORMAL_PLAN)}
    result = await run_attempt(case("normal-build-week"), 1, settings, models, timeout_s=0.001)
    assert result.status == "error"
    assert result.error is not None and "did not finish" in result.error


# --- the judge -------------------------------------------------------------


def judge_saying(passed: bool, calls: list[str] | None = None) -> FunctionModel:
    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if calls is not None:
            calls.append(str(messages[0]))
        return ModelResponse(
            parts=[TextPart(json.dumps({"passed": passed, "reason": "Saw the question."}))]
        )

    return fake_model(respond)


@pytest.mark.anyio
async def test_judge_pass_and_fail_decide_the_result(settings: Settings) -> None:
    layoff = case("three-week-layoff")
    models = {"analyst": answers(LAYOFF_ANALYST), "planner": answers(GOOD_LAYOFF_PLAN)}
    passed = await run_attempt(layoff, 1, settings, models, judge_saying(True))
    failed = await run_attempt(layoff, 1, settings, models, judge_saying(False))
    assert passed.status == "pass"
    assert failed.status == "fail"
    assert failed.checks[-1].kind == "judge"
    assert failed.checks[-1].detail == "Saw the question."


@pytest.mark.anyio
async def test_judge_sees_the_rubric_and_output_but_not_the_metrics(settings: Settings) -> None:
    calls: list[str] = []
    models = {"analyst": answers(LAYOFF_ANALYST), "planner": answers(GOOD_LAYOFF_PLAN)}
    await run_attempt(case("three-week-layoff"), 1, settings, models, judge_saying(True, calls))
    assert "asks the runner why they stopped" in calls[0]
    assert "Why did you stop running?" in calls[0]
    assert "total_cost_usd" not in calls[0]


@pytest.mark.anyio
async def test_judge_is_not_called_when_a_deterministic_check_failed(settings: Settings) -> None:
    calls: list[str] = []
    over = plan([0, 5, 0, 5, 0, 5, 0], "Why did you stop?")
    models = {"analyst": answers(LAYOFF_ANALYST), "planner": answers(over)}
    result = await run_attempt(
        case("three-week-layoff"), 1, settings, models, judge_saying(True, calls)
    )
    assert calls == []
    assert result.status == "fail"
    assert result.checks[-1].status == "skipped"


@pytest.mark.anyio
async def test_a_broken_judge_is_an_error_not_a_fail(settings: Settings) -> None:
    def broken(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("not json")])

    models = {"analyst": answers(LAYOFF_ANALYST), "planner": answers(GOOD_LAYOFF_PLAN)}
    result = await run_attempt(case("three-week-layoff"), 1, settings, models, fake_model(broken))
    assert result.status == "error"


# --- dry run ---------------------------------------------------------------


@pytest.mark.anyio
async def test_dry_run_catches_a_wrong_expected_value(settings: Settings) -> None:
    good = case("normal-build-week")
    wrong = good.model_copy(
        update={
            "criteria": [
                FieldEquals(type="field_equals", field="analyst.distance_km", expected=99),
                PlanTotalAtMost(type="plan_total_km_at_most", km=100),
            ]
        }
    )
    problems = await dry_run_case(wrong, settings)
    assert any("distance_km" in p and "35" in p for p in problems)
    assert any("above the server's ceiling" in p for p in problems)


@pytest.mark.anyio
async def test_dry_run_reports_data_the_server_rejects(settings: Settings) -> None:
    good = case("normal-build-week")
    bad_input = good.input.model_copy(update={"activities": [{"id": 1, "type": "Run"}]})
    problems = await dry_run_case(good.model_copy(update={"input": bad_input}), settings)
    assert any("Malformed activity" in p for p in problems)


def test_main_dry_run_makes_no_llm_calls_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["--dry-run"]) == 0
    assert "No LLM calls" in capsys.readouterr().out


def test_main_rejects_an_unknown_case(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--dry-run", "--case", "nope"]) == 2
    assert "Unknown case ids" in capsys.readouterr().err


# --- results and reporting -------------------------------------------------


def attempt(status: str, cost: float | None = 0.01) -> AttemptResult:
    return AttemptResult.model_validate(
        {
            "case_id": f"case-{status}",
            "category": "normal",
            "attempt": 1,
            "status": status,
            "checks": [],
            "report": None,
            "error": "Rate limited." if status == "error" else None,
            "cost_usd": cost,
            "latency_s": 1.5,
        }
    )


def test_errors_stay_out_of_the_pass_rate() -> None:
    result = summarise([attempt("pass"), attempt("fail"), attempt("error", None)], 1)
    assert (result.passed, result.failed, result.errors) == (1, 1, 1)
    assert result.pass_rate_pct == 50.0
    assert result.total_cost_usd is None  # an errored attempt's spend is unknown


def test_nothing_scored_has_no_rate() -> None:
    assert summarise([attempt("error")], 1).pass_rate_pct is None


def test_table_shows_each_case_the_total_and_the_error() -> None:
    table = format_table(summarise([attempt("pass"), attempt("error", 0.02)], 1))
    assert "case-pass" in table and "PASS" in table
    assert "Passed 1/1 (100.0%), errors: 1" in table
    assert "Rate limited." in table


def test_results_are_saved_as_dated_json(tmp_path: Path) -> None:
    result = summarise([attempt("pass")], 1)
    path = save_results(result, tmp_path / "results")
    assert path.name.startswith(f"{result.created_at:%Y-%m-%d}")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["passed"] == 1
    assert saved["attempts"][0]["case_id"] == "case-pass"


def test_progress_line_names_the_case_and_result() -> None:
    assert progress_line(2, 5, attempt("pass")) == "[2/5] case-pass PASS (1.5s)"


def test_pure_eval_arithmetic() -> None:
    assert plan_total_km([7, 7.5, 0]) == 14.5
    assert pass_rate_pct(2, 3) == 66.7
    assert pass_rate_pct(0, 0) is None


def test_real_cases_are_never_tracked_by_git() -> None:
    for path in ("evals/cases.real.json", "evals/results/x.json", "data/goal.json"):
        result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False)
        assert result.returncode == 0, path


def test_real_cases_match_the_schema_if_present() -> None:
    path = ROOT / "evals/cases.real.json"
    if not path.exists():
        pytest.skip("evals/cases.real.json is local only")
    assert len(load_suite(path).cases) >= 1
