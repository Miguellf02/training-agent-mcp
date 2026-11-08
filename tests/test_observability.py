"""What traces contain. The default must carry tokens, latency and cost, and never content."""

import json
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from pydantic_ai import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo
from test_agents import (
    WEEK,
    analyst_function,
    analyst_json,
    fake_model,
    planner_function,
)

from coach.agents.runner import RunnerError, run_week
from coach.config import Settings
from coach.models import ModelPrice, TokenRates
from coach.observability import configure_observability, send_to_logfire_mode

ROOT = Path(__file__).resolve().parents[1]
# All of these appear in prompts, tool results or outputs during a run.
CONTENT = ["data analyst", "cautious running coach", "Secret Race", "A normal week", "Under the"]


class Collector(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def dump(self) -> str:
        """Everything a backend would receive, as text."""
        return "\n".join(span.to_json() for span in self.spans)

    def named(self, prefix: str) -> list[ReadableSpan]:
        return [span for span in self.spans if span.name.startswith(prefix)]


def make_settings(tmp_path: Path, *, log_content: bool = False) -> Settings:
    goal = tmp_path / "goal.json"
    goal.write_text(
        json.dumps({"race": "Secret Race", "race_date": "2026-08-30", "distance_km": 10}),
        encoding="utf-8",
    )
    return Settings(
        fixtures_path=ROOT / "evals/synthetic/build_week.json",
        goal_path=goal,
        log_content=log_content,
        model_prices={
            "function:analyst_function:": ModelPrice(
                rates=TokenRates(input=1.0, output=1.0, cache_write=1.0, cache_read=1.0)
            ),
            "function:planner_function:": ModelPrice(
                rates=TokenRates(input=1.0, output=1.0, cache_write=1.0, cache_read=1.0)
            ),
        },
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def collector() -> Iterator[Collector]:
    yield Collector()
    # Later tests must not keep this test's instrumentation or exporter.
    import logfire

    logfire.configure(send_to_logfire=False, console=False, inspect_arguments=False)
    logfire.instrument_pydantic_ai(include_content=False)


def models() -> dict[str, object]:
    return {"analyst": fake_model(analyst_function), "planner": fake_model(planner_function)}


async def traced_run(settings: Settings, collector: Collector) -> None:
    configure_observability(settings, "test", span_processors=[SimpleSpanProcessor(collector)])
    await run_week(WEEK, settings, models())  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_default_traces_carry_no_content(tmp_path: Path, collector: Collector) -> None:
    await traced_run(make_settings(tmp_path), collector)
    dump = collector.dump()
    assert collector.spans, "the run produced no spans, so this test proves nothing"
    leaked = [text for text in CONTENT if text in dump]
    assert leaked == []


@pytest.mark.anyio
async def test_content_flag_does_record_content(tmp_path: Path, collector: Collector) -> None:
    # The control for the test above: the same check does find content when it is on.
    await traced_run(make_settings(tmp_path, log_content=True), collector)
    dump = collector.dump()
    assert "data analyst" in dump
    assert "A normal week" in dump


@pytest.mark.anyio
async def test_content_log_holds_prompts_and_outputs_and_stdout_stays_clean(
    tmp_path: Path, collector: Collector, capfd: pytest.CaptureFixture[str]
) -> None:
    # coach-week prints its JSON on stdout, so debug output must never mix with it.
    log = tmp_path / "logs/debug.jsonl"
    settings = make_settings(tmp_path, log_content=True).model_copy(
        update={"content_log_path": log}
    )
    configure_observability(settings, "test", write_content_log=True)
    await run_week(WEEK, settings, models())  # type: ignore[arg-type]
    assert capfd.readouterr().out == ""
    text = log.read_text(encoding="utf-8")
    assert "data analyst" in text  # a prompt
    assert "A normal week" in text  # an output
    assert all(json.loads(line) for line in text.splitlines())


@pytest.mark.anyio
async def test_no_content_log_without_the_flag(tmp_path: Path, collector: Collector) -> None:
    log = tmp_path / "debug.jsonl"
    settings = make_settings(tmp_path).model_copy(update={"content_log_path": log})
    configure_observability(settings, "test", write_content_log=True)
    await run_week(WEEK, settings, models())  # type: ignore[arg-type]
    assert not log.exists()


def test_content_log_is_git_ignored() -> None:
    default = Settings(_env_file=None).content_log_path  # type: ignore[call-arg]
    result = subprocess.run(["git", "check-ignore", "-q", str(default)], cwd=ROOT, check=False)
    assert result.returncode == 0


def test_content_mode_never_sends_to_logfire(tmp_path: Path) -> None:
    assert send_to_logfire_mode(make_settings(tmp_path, log_content=True)) is False
    assert send_to_logfire_mode(make_settings(tmp_path)) == "if-token-present"


@pytest.mark.anyio
async def test_each_llm_call_and_tool_call_has_a_span(tmp_path: Path, collector: Collector) -> None:
    await traced_run(make_settings(tmp_path), collector)
    # Analyst: a tool call and a final answer. Planner: the same.
    assert len(collector.named("chat ")) == 4
    tools = " ".join(span.name for span in collector.spans if "tool" in span.name)
    assert "mcp__coach__get_weekly_load" in tools
    assert "ReadMcpResourceTool" in tools


@pytest.mark.anyio
async def test_spans_and_report_carry_tokens_and_cost(tmp_path: Path, collector: Collector) -> None:
    settings = make_settings(tmp_path)
    configure_observability(settings, "test", span_processors=[SimpleSpanProcessor(collector)])
    report = await run_week(WEEK, settings, models())  # type: ignore[arg-type]

    analyst, planner = report.metrics.agents
    assert (analyst.agent, planner.agent) == ("analyst", "planner")
    assert (analyst.requests, planner.requests) == (2, 2)
    assert analyst.usage.prompt_tokens > 0
    assert analyst.cost_usd is not None and analyst.cost_usd > 0
    assert report.metrics.total_cost_usd == pytest.approx(
        analyst.cost_usd + (planner.cost_usd or 0), abs=1e-6
    )

    root = collector.named("week ")[0]
    assert root.attributes is not None
    assert root.attributes["coach.cost_usd"] == report.metrics.total_cost_usd
    assert root.attributes["coach.prompt_tokens"] == (
        analyst.usage.prompt_tokens + planner.usage.prompt_tokens
    )
    assert len(collector.named("agent ")) == 2


@pytest.mark.anyio
async def test_unpriced_model_has_null_cost(tmp_path: Path) -> None:
    settings = make_settings(tmp_path).model_copy(update={"model_prices": {}})
    report = await run_week(WEEK, settings, models())  # type: ignore[arg-type]
    assert report.metrics.total_cost_usd is None
    assert report.metrics.agents[0].cost_usd is None


@pytest.mark.anyio
async def test_failure_messages_do_not_leak_into_traces(
    tmp_path: Path, collector: Collector
) -> None:
    # The analyst's notes reach the RunnerError, which the user sees, but not the trace.
    def analyst(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        failed = json.loads(analyst_json())
        failed.update(next_week_start=None, notes="Private note from the model.")
        return ModelResponse(parts=[TextPart(json.dumps(failed))])

    settings = make_settings(tmp_path)
    configure_observability(settings, "test", span_processors=[SimpleSpanProcessor(collector)])
    run_models = {"analyst": fake_model(analyst), "planner": fake_model(planner_function)}
    with pytest.raises(RunnerError, match="Private note"):
        await run_week(WEEK, settings, run_models)
    assert "Private note" not in collector.dump()
    root = collector.named("week ")[0]
    assert root.attributes is not None
    assert root.attributes["coach.error_type"] == "RunnerError"


@pytest.mark.anyio
async def test_invalid_model_output_does_not_leak_into_traces(
    tmp_path: Path, collector: Collector
) -> None:
    def analyst(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("Private invalid answer.")])

    settings = make_settings(tmp_path)
    configure_observability(settings, "test", span_processors=[SimpleSpanProcessor(collector)])
    run_models = {"analyst": fake_model(analyst), "planner": fake_model(planner_function)}
    with pytest.raises(RunnerError):
        await run_week(WEEK, settings, run_models)
    assert "Private invalid answer" not in collector.dump()
