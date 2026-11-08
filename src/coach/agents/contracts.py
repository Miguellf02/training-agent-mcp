"""What each agent must return. Evals check these fields, never free text."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from coach.models import Flag, TokenUsage

Day = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEK_DAYS: tuple[Day, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AnalystReport(_Contract):
    """The analyst's JSON contract. Numbers are null only when the tools failed."""

    week: str
    distance_km: float | None
    runs: int | None
    longest_run_km: float | None
    change_vs_prior_avg_pct: float | None
    zero_weeks_streak: int | None
    data_complete: bool | None
    blocked_count: int | None
    flags: list[Flag]
    inferred_flags: list[Literal["race_detected"]]
    suggested_max_next_week_km: float | None
    fallback_max_next_week_km: float | None
    next_week_start: dt.date | None
    notes: str


class PlanDay(_Contract):
    day: Day
    session: str
    approx_km: float = Field(ge=0)


class WeekPlan(_Contract):
    """The planner's output: one entry per day, Monday to Sunday."""

    question_for_runner: str | None
    days: list[PlanDay]
    reasoning: str

    @field_validator("days")
    @classmethod
    def _one_entry_per_day_in_order(cls, days: list[PlanDay]) -> list[PlanDay]:
        if tuple(d.day for d in days) != WEEK_DAYS:
            raise ValueError("days must have exactly 7 entries, in order from Mon to Sun")
        return days


class AgentMetrics(_Contract):
    """What one agent consumed in a run."""

    agent: str
    model: str
    requests: int
    usage: TokenUsage
    latency_s: float
    cost_usd: float | None = Field(description="Estimate. Null if the model has no known price.")


class RunMetrics(_Contract):
    agents: list[AgentMetrics]
    total_cost_usd: float | None = Field(description="Null if any agent's cost is unknown.")


class WeekReport(_Contract):
    """One complete run: what happened, the plan for the next week, and what it cost."""

    analyst: AnalystReport
    plan: WeekPlan
    metrics: RunMetrics
