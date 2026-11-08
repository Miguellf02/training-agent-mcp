"""Run analyst, then planner, with validated outputs.

The agent definitions stay in .claude/agents/*.md, the same files /week uses: model, tool
allowlist and prompt all come from there. Only the planner's output section is replaced,
because it asks for markdown and this runner needs validated fields.
"""

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import logfire
from fastmcp.client.transports import StdioTransport
from pydantic import BaseModel, ConfigDict
from pydantic_ai import (
    Agent,
    ModelResponse,
    ModelRetry,
    NativeOutput,
    RunContext,
    UsageLimits,
)
from pydantic_ai.exceptions import AgentRunError, UserError
from pydantic_ai.mcp import MCPError, MCPToolset
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset

from coach.agents.contracts import (
    AgentMetrics,
    AnalystReport,
    RunMetrics,
    WeekPlan,
    WeekReport,
)
from coach.analytics import (
    elapsed_s,
    parse_iso_week,
    requests_cost_usd,
    sum_usage,
    total_cost_usd,
)
from coach.config import PROJECT_ROOT, Settings
from coach.models import TokenUsage
from coach.observability import configure_observability

AGENTS_DIR = PROJECT_ROOT / ".claude/agents"
MODEL_IDS: Mapping[str, str] = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}
RESOURCE_TOOL = "ReadMcpResourceTool"
# Combined with PrefixedToolset's "_", this gives the names the prompts use.
TOOL_PREFIX = "mcp__coach_"
MAX_REQUESTS_PER_AGENT = 10
MAX_OUTPUT_TOKENS = 16000

PLANNER_OUTPUT = """\
## Output

Return the structured plan:
- `question_for_runner`: the question for the runner, only when flags contain layoff; else null.
- `days`: seven entries, Mon to Sun, each with `day`, `session` and `approx_km`.
- `reasoning`: 2-3 sentences: the ceiling you respected, why this load, how days_to_race
  (counted from next_week_start) shapes it.
"""


class RunnerError(Exception):
    """A failure whose message is safe to show and says what to do next."""


class AgentSpec(BaseModel):
    """One agent definition, parsed from its markdown file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    model: str
    tools: frozenset[str]
    instructions: str


def load_agent_spec(name: str, agents_dir: Path = AGENTS_DIR) -> AgentSpec:
    """Parse the frontmatter (name, tools, model) and the prompt of an agent file."""
    text = (agents_dir / f"{name}.md").read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---\n", 2)
    fields = dict(line.split(": ", 1) for line in frontmatter.splitlines() if ": " in line)
    return AgentSpec(
        name=fields["name"],
        model=fields["model"],
        tools=frozenset(tool.strip() for tool in fields["tools"].split(",")),
        instructions=body.strip(),
    )


def planner_instructions(spec: AgentSpec) -> str:
    """The planner's prompt with its markdown output section replaced."""
    rules, separator, _ = spec.instructions.partition("## Output")
    if not separator:
        raise RunnerError(f"{spec.name}.md has no '## Output' section to replace.")
    return rules + PLANNER_OUTPUT


def build_model(alias: str, settings: Settings) -> Model:
    if alias not in MODEL_IDS:
        raise RunnerError(f"Unknown model {alias!r} in an agent file; use one of {set(MODEL_IDS)}.")
    if settings.anthropic_api_key is None:
        raise RunnerError("Set ANTHROPIC_API_KEY in .env or the environment to run the agents.")
    provider = AnthropicProvider(api_key=settings.anthropic_api_key.get_secret_value())
    return AnthropicModel(MODEL_IDS[alias], provider=provider)


def _server_toolset(settings: Settings) -> MCPToolset[None]:
    """The coach MCP server as a subprocess, using this run's data files."""
    env = {**os.environ, "COACH_GOAL_PATH": str(settings.goal_path)}
    if settings.fixtures_path is not None:
        env["COACH_FIXTURES_PATH"] = str(settings.fixtures_path)
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "coach.server"],
        env=env,
        cwd=str(PROJECT_ROOT),
        keep_alive=False,  # otherwise each run leaves a server process behind
    )
    return MCPToolset(transport)


def _build_agent[OutputT: BaseModel](
    spec: AgentSpec,
    instructions: str,
    output_type: type[OutputT],
    model: Model,
    settings: Settings,
) -> Agent[None, OutputT]:
    coach_server = _server_toolset(settings)

    def allowed(_ctx: RunContext[None], tool: ToolDefinition) -> bool:
        return tool.name in spec.tools

    toolset: AbstractToolset[None] = coach_server.prefixed(TOOL_PREFIX).filtered(allowed)
    agent: Agent[None, OutputT] = Agent(
        model,
        name=spec.name,
        instructions=instructions,
        output_type=NativeOutput(output_type),
        toolsets=[toolset],
        retries=1,
        model_settings=ModelSettings(max_tokens=MAX_OUTPUT_TOKENS),
    )

    if RESOURCE_TOOL in spec.tools:

        async def read_mcp_resource(server: str, uri: str) -> str:
            """Read a resource from an MCP server. The only server is 'coach'."""
            if server != "coach":
                raise ModelRetry(f"Unknown server {server!r}; use 'coach'.")
            try:
                content = await coach_server.read_resource(uri)
            except MCPError as exc:
                raise ModelRetry(f"Could not read {uri}: {exc}") from exc
            if not isinstance(content, str):
                raise ModelRetry(f"{uri} did not return text.")
            return content

        agent.tool_plain(name=RESOURCE_TOOL)(read_mcp_resource)
    return agent


def _usage_attributes(usage: TokenUsage, cost_usd: float | None) -> dict[str, int | float | None]:
    return {
        "coach.prompt_tokens": usage.prompt_tokens,
        "coach.output_tokens": usage.output_tokens,
        "coach.cache_read_tokens": usage.cache_read_tokens,
        "coach.cache_write_tokens": usage.cache_write_tokens,
        "coach.cost_usd": cost_usd,
    }


async def run_agent[OutputT: BaseModel](
    agent: Agent[None, OutputT], name: str, prompt: str, settings: Settings
) -> tuple[OutputT, AgentMetrics]:
    model = agent.model
    model_name = model.model_name if isinstance(model, Model) else str(model)
    failure: RunnerError
    with logfire.span("agent {agent}", agent=name, model=model_name) as span:
        started = time.perf_counter()
        try:
            result = await agent.run(
                prompt, usage_limits=UsageLimits(request_limit=MAX_REQUESTS_PER_AGENT)
            )
        except (AgentRunError, UserError) as exc:
            failure = RunnerError(f"The {name} run failed: {exc}")
            failure.__cause__ = exc
            span.set_level("error")
            span.set_attribute("coach.error_type", type(exc).__name__)
        else:
            usages = [
                TokenUsage(
                    prompt_tokens=message.usage.input_tokens,
                    output_tokens=message.usage.output_tokens,
                    cache_read_tokens=message.usage.cache_read_tokens,
                    cache_write_tokens=message.usage.cache_write_tokens,
                )
                for message in result.all_messages()
                if isinstance(message, ModelResponse)
            ]
            metrics = AgentMetrics(
                agent=name,
                model=model_name,
                requests=len(usages),
                usage=sum_usage(usages),
                latency_s=elapsed_s(started, time.perf_counter()),
                cost_usd=requests_cost_usd(usages, settings.model_prices.get(model_name)),
            )
            span.set_attributes(_usage_attributes(metrics.usage, metrics.cost_usd))
            return result.output, metrics
    raise failure


async def run_week(
    week: str,
    settings: Settings | None = None,
    models: Mapping[str, Model] | None = None,
) -> WeekReport:
    """Run analyst then planner for one ISO week. `models` overrides them by agent name."""
    try:
        parse_iso_week(week)
    except ValueError as exc:
        raise RunnerError(str(exc)) from exc
    active = settings if settings is not None else Settings()
    overrides = models if models is not None else {}
    analyst_spec = load_agent_spec("analyst")
    planner_spec = load_agent_spec("planner")

    analyst_model = overrides.get("analyst") or build_model(analyst_spec.model, active)
    planner_model = overrides.get("planner") or build_model(planner_spec.model, active)
    analyst = _build_agent(
        analyst_spec, analyst_spec.instructions, AnalystReport, analyst_model, active
    )
    planner = _build_agent(
        planner_spec, planner_instructions(planner_spec), WeekPlan, planner_model, active
    )

    # Errors are raised outside the spans: a span records the message of any exception crossing
    # it, and these messages can quote model output. Traces get only the error type.
    failure: RunnerError
    with logfire.span("week {week}", week=week) as root:
        try:
            report, analyst_metrics = await run_agent(analyst, "analyst", week, active)
            if report.next_week_start is None:
                raise RunnerError(f"The analyst could not read {week}: {report.notes}")
            prompt = (
                f"Analyst JSON for {week}:\n{report.model_dump_json()}\n"
                "Plan the week starting on next_week_start."
            )
            plan, planner_metrics = await run_agent(planner, "planner", prompt, active)
        except RunnerError as exc:
            failure = exc
            root.set_level("error")
            root.set_attribute("coach.error_type", type(exc).__name__)
        else:
            agents = [analyst_metrics, planner_metrics]
            total = total_cost_usd([a.cost_usd for a in agents])
            usage = sum_usage([a.usage for a in agents])
            root.set_attributes(_usage_attributes(usage, total))
            return WeekReport(
                analyst=report,
                plan=plan,
                metrics=RunMetrics(agents=agents, total_cost_usd=total),
            )
    raise failure


def main() -> int:
    parser = argparse.ArgumentParser(description="Review one ISO week and plan the next one.")
    parser.add_argument("week", help="ISO week, e.g. 2026-W30")
    args = parser.parse_args()
    settings = Settings()
    configure_observability(settings, "coach-agents", write_content_log=True)
    try:
        report = asyncio.run(run_week(str(args.week), settings))
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        logfire.force_flush()
        if settings.log_content:
            print(f"Content log: {settings.content_log_path}", file=sys.stderr)
    print(report.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
