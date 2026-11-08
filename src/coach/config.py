"""Settings, read once from environment variables (or a local .env file)."""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from coach.models import ModelPrice, TokenRates

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# USD per million tokens, from https://platform.claude.com/docs/en/about-claude/pricing
# Reviewed 2026-11-08. Standard API rates: no batch, fast mode or data-residency multipliers.
DEFAULT_MODEL_PRICES: dict[str, ModelPrice] = {
    "claude-haiku-5-5": ModelPrice(
        rates=TokenRates(input=0.10, output=0.50, cache_write=0.125, cache_read=0.01),
        long_prompt_threshold_tokens=100_000,
        long_prompt_rates=TokenRates(input=0.50, output=2.50, cache_write=0.625, cache_read=0.05),
    ),
    "claude-sonnet-5-5": ModelPrice(
        rates=TokenRates(input=2.00, output=10.00, cache_write=2.50, cache_read=0.10),
    ),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COACH_",
        env_file=PROJECT_ROOT / ".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    intervals_api_key: SecretStr | None = Field(
        default=None, description="Intervals.icu API key. Not needed in fixtures mode."
    )
    intervals_athlete_id: str = Field(default="0", description="'0' means the key's owner.")
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
        description="Used only by the Python agent runner; the MCP server does not need it.",
    )
    logfire_token: SecretStr | None = Field(
        default=None,
        validation_alias="LOGFIRE_TOKEN",
        description="Write token for Logfire. Without it, no trace leaves this machine.",
    )
    log_content: bool = Field(
        default=False,
        description="Debug mode: writes prompts and outputs to content_log_path and sends "
        "nothing to Logfire. Off by default, when traces carry only tokens, latency and cost.",
    )
    content_log_path: Path = Field(
        default=PROJECT_ROOT / "logs/debug-traces.jsonl",
        description="Local file for COACH_LOG_CONTENT. Git-ignored; it holds prompts and outputs.",
    )
    model_prices: dict[str, ModelPrice] = Field(
        default_factory=lambda: dict(DEFAULT_MODEL_PRICES),
        description="Prices by model id. A model not listed here gets a null cost.",
    )
    fixtures_path: Path | None = Field(
        default=None, description="If set, read activities from this JSON file instead of the API."
    )
    goal_path: Path = Field(default=PROJECT_ROOT / "data/goal.json")
    threshold_pace_sec_per_km: float = Field(
        default=310.0, gt=0, description="Threshold pace used for the load estimate (5:10/km)."
    )
    max_range_days: int = Field(default=31, gt=0)
    spike_threshold_pct: float = Field(
        default=30.0, gt=0, description="Week-over-average increase (%) that counts as a spike."
    )
    layoff_min_zero_weeks: int = Field(
        default=2, ge=1, description="Consecutive 0 km weeks that count as a layoff."
    )
    max_weekly_increase_pct: float = Field(
        default=10.0, ge=0, description="Max increase (%) used for the next week's km ceiling."
    )
    trend_window_weeks: int = Field(
        default=4,
        ge=2,
        description="Weeks (target included) behind % change, flags and ceiling, fixed so the "
        "trend does not depend on how many weeks a caller asks for.",
    )
    streak_lookback_weeks: int = Field(
        default=12,
        ge=1,
        description="Weeks (target included) searched for the zero-week streak, fixed so the "
        "streak does not depend on how many weeks a caller asks for. It also caps the streak.",
    )

    incomplete_data_max_km: float = Field(
        default=10.0,
        gt=0,
        description="Cap for next week's km when data is incomplete and the real load is "
        "unknown. A cautious project choice, not a validated training rule.",
    )

    @field_validator("fixtures_path", "goal_path", "content_log_path")
    @classmethod
    def _anchor_to_project_root(cls, path: Path | None) -> Path | None:
        """Relative paths mean relative to the repo, not to wherever the process started."""
        return path if path is None or path.is_absolute() else PROJECT_ROOT / path
