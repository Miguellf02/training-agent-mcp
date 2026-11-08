"""Pure functions. All arithmetic lives here, never in the LLM.

No I/O, no globals: everything is a function of its inputs, so it is trivial to test.
"""

import datetime as dt
import re

from coach.models import (
    Activity,
    BlockedActivity,
    Flag,
    Goal,
    GoalConfig,
    LoadPolicy,
    ModelPrice,
    RawActivity,
    TokenUsage,
    WeeklyLoadResult,
    WeekSummary,
)

_TOKENS_PER_PRICE_UNIT = 1_000_000
_COST_DECIMALS = 6

_ISO_WEEK = re.compile(r"^(\d{4})-W(\d{2})$")


def parse_iso_week(week: str) -> dt.date:
    """'2026-W42' -> Monday of that week. Raises ValueError on bad input."""
    match = _ISO_WEEK.match(week)
    if not match:
        raise ValueError(f"Invalid ISO week {week!r}; expected format like '2026-W42'.")
    year, num = int(match.group(1)), int(match.group(2))
    return dt.date.fromisocalendar(year, num, 1)  # also raises ValueError for W54 etc.


def iso_week_label(day: dt.date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def format_pace(sec_per_km: float) -> str:
    total = round(sec_per_km)
    return f"{total // 60}:{total % 60:02d}"


def estimate_load(distance_km: float, moving_time_s: float, threshold_pace_s: float) -> float:
    """rTSS-like load from pace alone (no HR needed): hours x intensity^2 x 100.

    intensity = threshold pace / actual pace, so one hour at threshold pace = 100.
    """
    if distance_km <= 0 or moving_time_s <= 0:
        return 0.0
    pace_s = moving_time_s / distance_km
    intensity = threshold_pace_s / pace_s
    return round((moving_time_s / 3600) * intensity**2 * 100, 1)


def normalise(raw: RawActivity, threshold_pace_s: float) -> Activity:
    distance_km = raw.distance_m / 1000
    pace_s = raw.moving_time_s / distance_km if distance_km > 0 else 0.0
    return Activity(
        id=raw.id,
        date=raw.date,
        name=raw.name,
        distance_km=round(distance_km, 2),
        moving_time_min=round(raw.moving_time_s / 60, 1),
        pace_min_per_km=format_pace(pace_s) if pace_s else "n/a",
        avg_heart_rate_bpm=round(raw.average_heartrate) if raw.average_heartrate else None,
        est_load=estimate_load(distance_km, raw.moving_time_s, threshold_pace_s),
    )


def summarise_week(
    monday: dt.date, activities: list[Activity], blocked: list[BlockedActivity]
) -> WeekSummary:
    sunday = monday + dt.timedelta(days=6)
    in_week = [a for a in activities if monday <= a.date <= sunday]
    return WeekSummary(
        week=iso_week_label(monday),
        start=monday,
        end=sunday,
        runs=len(in_week),
        distance_km=round(sum(a.distance_km for a in in_week), 2),
        moving_time_h=round(sum(a.moving_time_min for a in in_week) / 60, 2),
        longest_run_km=max((a.distance_km for a in in_week), default=0.0),
        est_load=round(sum(a.est_load for a in in_week), 1),
        blocked_activities=sum(monday <= b.date <= sunday for b in blocked),
    )


def load_flags(
    change_pct: float | None,
    zero_weeks_streak: int,
    data_complete: bool,
    target_missing_hr: bool,
    policy: LoadPolicy,
) -> list[Flag]:
    """Deterministic flags for the target week, in a fixed order."""
    flags: list[Flag] = []
    if change_pct is not None and change_pct > policy.spike_threshold_pct:
        flags.append("load_spike")
    if zero_weeks_streak >= policy.layoff_min_zero_weeks:
        flags.append("layoff")
    if not data_complete:
        flags.append("incomplete_data")
    if target_missing_hr:
        flags.append("missing_hr")
    return flags


def next_week_ceiling_km(
    weeks: list[WeekSummary], flags: list[Flag], policy: LoadPolicy
) -> float | None:
    """Ceiling for next week's distance.

    Recent average = mean distance of the trend window (target included).
    - Incomplete data: None (the totals undercount, so any ceiling would be wrong).
    - After a load_spike or a layoff: the recent average, no increase.
    - Otherwise: max(recent average, target week) x (1 + max_weekly_increase_pct / 100).
      The max() keeps a week that follows empty weeks from getting a ceiling below itself.
    """
    if "incomplete_data" in flags:
        return None
    recent_avg = sum(w.distance_km for w in weeks) / len(weeks)
    if "load_spike" in flags or "layoff" in flags:
        return round(recent_avg, 1)
    base = max(recent_avg, weeks[-1].distance_km)
    return round(base * (1 + policy.max_weekly_increase_pct / 100), 1)


def request_cost_usd(usage: TokenUsage, price: ModelPrice) -> float:
    """Estimated cost of one request. Long-prompt prices apply per request, to all its tokens.

    prompt_tokens includes the cached tokens, which have their own prices, so they are
    subtracted to get the tokens charged at the base input price. Cache writes use the
    5-minute price: the runner sets no caching, so these are normally zero.
    """
    threshold = price.long_prompt_threshold_tokens
    is_long = threshold is not None and usage.prompt_tokens > threshold
    rates = price.long_prompt_rates if is_long and price.long_prompt_rates else price.rates
    uncached = max(0, usage.prompt_tokens - usage.cache_read_tokens - usage.cache_write_tokens)
    per_unit = (
        uncached * rates.input
        + usage.cache_read_tokens * rates.cache_read
        + usage.cache_write_tokens * rates.cache_write
        + usage.output_tokens * rates.output
    )
    return per_unit / _TOKENS_PER_PRICE_UNIT


def requests_cost_usd(usages: list[TokenUsage], price: ModelPrice | None) -> float | None:
    """Estimated cost of several requests, or None when the model has no known price."""
    if price is None:
        return None
    return round(sum(request_cost_usd(usage, price) for usage in usages), _COST_DECIMALS)


def total_cost_usd(costs: list[float | None]) -> float | None:
    """Sum of costs. Unknown if any part is unknown: a partial total would look complete."""
    known = [cost for cost in costs if cost is not None]
    return round(sum(known), _COST_DECIMALS) if len(known) == len(costs) else None


def sum_usage(usages: list[TokenUsage]) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=sum(u.prompt_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        cache_read_tokens=sum(u.cache_read_tokens for u in usages),
        cache_write_tokens=sum(u.cache_write_tokens for u in usages),
    )


def elapsed_s(start_s: float, end_s: float) -> float:
    return round(end_s - start_s, 3)


def plan_total_km(day_kms: list[float]) -> float:
    return round(sum(day_kms), 2)


def pass_rate_pct(passed: int, scored: int) -> float | None:
    """Share of scored attempts that passed, or None when nothing could be scored."""
    return round(passed / scored * 100, 1) if scored > 0 else None


def goal_status(config: GoalConfig, as_of: dt.date) -> Goal:
    """The race with days_to_race counted from as_of."""
    return Goal(**config.model_dump(), as_of=as_of, days_to_race=(config.race_date - as_of).days)


def history_weeks(n_weeks: int, policy: LoadPolicy) -> int:
    """Weeks of history needed, target included: the listing, the trend and the streak."""
    return max(n_weeks, policy.trend_window_weeks, policy.streak_lookback_weeks)


def weekly_load(
    activities: list[Activity],
    blocked: list[BlockedActivity],
    target_monday: dt.date,
    n_weeks: int,
    policy: LoadPolicy,
) -> WeeklyLoadResult:
    """Summaries for the target week and the (n_weeks - 1) weeks before it, oldest first,
    plus deterministic flags and a ceiling for next week.

    The trend (% change, data_complete, flags, ceiling) always uses a fixed window of
    policy.trend_window_weeks ending at the target, so it does not depend on n_weeks:
    asking for more or fewer weeks only changes how many are listed. The zero-week streak
    likewise searches a fixed policy.streak_lookback_weeks, which also caps it.

    Blocked activities exist but are unreadable, so a week containing one is never treated
    as a 0 km week, and the % change is withheld while any week undercounts.
    """
    span = history_weeks(n_weeks, policy)
    mondays = [target_monday - dt.timedelta(weeks=i) for i in reversed(range(span))]
    all_weeks = [summarise_week(m, activities, blocked) for m in mondays]
    window = all_weeks[-policy.trend_window_weeks :]
    data_complete = all(w.blocked_activities == 0 for w in window)

    target, prior = window[-1], window[:-1]
    prior_avg = sum(w.distance_km for w in prior) / len(prior)
    change = (
        round((target.distance_km - prior_avg) / prior_avg * 100, 1)
        if data_complete and prior_avg > 0
        else None
    )

    streak = 0
    for w in reversed(all_weeks):
        if w.distance_km > 0 or w.blocked_activities > 0:
            break
        streak += 1

    target_sunday = target_monday + dt.timedelta(days=6)
    target_missing_hr = any(
        a.avg_heart_rate_bpm is None for a in activities if target_monday <= a.date <= target_sunday
    )
    flags = load_flags(change, streak, data_complete, target_missing_hr, policy)

    return WeeklyLoadResult(
        weeks=all_weeks[-n_weeks:],
        target_week=target.week,
        change_vs_prior_avg_pct=change,
        zero_weeks_streak=streak,
        data_complete=data_complete,
        flags=flags,
        suggested_max_next_week_km=next_week_ceiling_km(window, flags, policy),
        fallback_max_next_week_km=None if data_complete else policy.incomplete_data_max_km,
        next_week_start=target_monday + dt.timedelta(weeks=1),
    )
