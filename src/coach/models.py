"""Domain models: the only shapes the agent ever sees.

Every unit is in the field name or description, so the model never has to guess
whether a number is metres or kilometres, seconds or minutes.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Flag = Literal["load_spike", "layoff", "incomplete_data", "missing_hr"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RawActivity(BaseModel):
    """The subset of an Intervals.icu activity we use. Unknown fields are ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    date: dt.date = Field(validation_alias="start_date_local")
    type: str
    name: str = ""
    distance_m: float = Field(default=0.0, ge=0, validation_alias="distance")
    moving_time_s: float = Field(default=0.0, ge=0, validation_alias="moving_time")
    average_heartrate: float | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _id_to_str(cls, v: object) -> str:
        return str(v)

    @field_validator("date", mode="before")
    @classmethod
    def _datetime_to_date(cls, v: object) -> object:
        # Intervals returns "2026-10-17T21:03:12"; we only need the day.
        return v[:10] if isinstance(v, str) else v


class LoadPolicy(_Frozen):
    """Thresholds behind the deterministic flags and the next-week ceiling (from Settings)."""

    spike_threshold_pct: float = Field(gt=0)
    layoff_min_zero_weeks: int = Field(ge=1)
    max_weekly_increase_pct: float = Field(ge=0)
    trend_window_weeks: int = Field(ge=2)
    streak_lookback_weeks: int = Field(ge=1)
    incomplete_data_max_km: float = Field(gt=0)


class Activity(_Frozen):
    """One run, normalised from the data source."""

    id: str
    date: dt.date
    name: str
    distance_km: float = Field(ge=0)
    moving_time_min: float = Field(ge=0)
    pace_min_per_km: str = Field(description="Average pace formatted as m:ss, e.g. '5:18'.")
    avg_heart_rate_bpm: int | None = Field(
        default=None, description="Null when the run was recorded without a HR sensor."
    )
    est_load: float = Field(
        ge=0, description="Pace-based training load (rTSS-like): 100 = one hour at threshold."
    )


class BlockedActivity(_Frozen):
    """An activity that exists in the source but whose data cannot be read."""

    id: str
    date: dt.date
    reason: str = Field(description="Why it is unreadable and how the runner can fix it.")


class ActivitiesResult(_Frozen):
    """Output of get_activities."""

    start: dt.date
    end: dt.date
    count: int
    activities: list[Activity]
    missing_heart_rate: int = Field(description="How many activities have no heart-rate data.")
    blocked: list[BlockedActivity] = Field(
        description="Activities that exist but could not be read. Never treat them as rest days."
    )


class WeekSummary(_Frozen):
    """Aggregated load for one ISO week (Monday to Sunday)."""

    week: str = Field(description="ISO week, e.g. '2026-W42'.")
    start: dt.date
    end: dt.date
    runs: int
    distance_km: float
    moving_time_h: float
    longest_run_km: float
    est_load: float
    blocked_activities: int = Field(
        description=(
            "Activities this week that exist but could not be read. If > 0, the totals "
            "undercount: the week is not a rest week."
        )
    )


class WeeklyLoadResult(_Frozen):
    """Output of get_weekly_load: the target week plus the weeks before it, oldest first."""

    weeks: list[WeekSummary]
    target_week: str
    change_vs_prior_avg_pct: float | None = Field(
        description=(
            "Distance change of the target week vs the average of the 3 weeks before it "
            "(fixed trend window, whatever n_weeks is), in %. "
            "Null when the previous weeks have zero distance (change is undefined) or when "
            "data_complete is false (the totals undercount)."
        )
    )
    zero_weeks_streak: int = Field(
        description=(
            "Consecutive weeks with 0 km and no blocked activities, ending at the target week "
            "(0 if it had runs or blocked activities). Does not depend on n_weeks; capped at "
            "the fixed lookback (12 weeks by default), so the cap means 'at least that many'."
        )
    )
    data_complete: bool = Field(
        description=(
            "False if any week of the trend window (target + 3 weeks before) has blocked "
            "activities: its totals undercount, so do not read low numbers there as rest."
        )
    )
    flags: list[Flag] = Field(
        description=(
            "Deterministic flags for the target week. load_spike: change_vs_prior_avg_pct is "
            "above the spike threshold. layoff: zero_weeks_streak reaches the layoff minimum. "
            "incomplete_data: data_complete is false. missing_hr: a run in the target week "
            "has no heart rate."
        )
    )
    suggested_max_next_week_km: float | None = Field(
        description=(
            "Ceiling for next week's distance (km). Recent average = mean distance of the "
            "trend window (target + 3 weeks before, whatever n_weeks is). Normal week: "
            "max(recent average, target week) x (1 + max weekly increase). After load_spike "
            "or layoff: the recent average, no increase. Null when data_complete is false "
            "(the totals undercount)."
        )
    )
    fallback_max_next_week_km: float | None = Field(
        description=(
            "Safety cap for next week (km) when data_complete is false, so a plan still has "
            "a limit set in code. It is a fixed setting, not derived from the runner's load, "
            "which is unknown. Null when data_complete is true."
        )
    )
    next_week_start: dt.date = Field(
        description="Monday of the week after the target week: the week being planned."
    )


class TokenRates(_Frozen):
    """Prices in USD per million tokens."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_write: float = Field(ge=0, description="5-minute cache write.")
    cache_read: float = Field(ge=0)


class ModelPrice(_Frozen):
    """A model's prices. Some models charge more per request above a prompt length."""

    rates: TokenRates
    long_prompt_threshold_tokens: int | None = None
    long_prompt_rates: TokenRates | None = None


class TokenUsage(_Frozen):
    """Tokens of one model request, or a sum of them."""

    prompt_tokens: int = Field(ge=0, description="All input tokens, cached ones included.")
    output_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0, description="Part of prompt_tokens.")
    cache_write_tokens: int = Field(ge=0, description="Part of prompt_tokens.")


class GoalConfig(_Frozen):
    """The race the plan is built towards, as stored in goal.json."""

    race: str
    race_date: dt.date
    distance_km: float = Field(gt=0)
    target_time: str | None = None


class Goal(GoalConfig):
    """The race plus the countdown from a given day."""

    as_of: dt.date = Field(description="The day days_to_race is counted from.")
    days_to_race: int = Field(description="Days from as_of to race_date.")
