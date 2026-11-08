"""Cost arithmetic, with prices fixed here so a price change cannot hide a formula bug."""

import pytest

from coach.agents.runner import MODEL_IDS
from coach.analytics import request_cost_usd, requests_cost_usd, sum_usage, total_cost_usd
from coach.config import DEFAULT_MODEL_PRICES
from coach.models import ModelPrice, TokenRates, TokenUsage

FLAT = ModelPrice(rates=TokenRates(input=2.0, output=10.0, cache_write=2.5, cache_read=0.1))
TIERED = ModelPrice(
    rates=TokenRates(input=1.0, output=5.0, cache_write=1.25, cache_read=0.1),
    long_prompt_threshold_tokens=100_000,
    long_prompt_rates=TokenRates(input=5.0, output=25.0, cache_write=6.25, cache_read=0.5),
)


def usage(prompt: int = 0, output: int = 0, read: int = 0, write: int = 0) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=prompt, output_tokens=output, cache_read_tokens=read, cache_write_tokens=write
    )


def test_input_and_output_use_their_own_prices() -> None:
    assert request_cost_usd(usage(prompt=1000, output=1000), FLAT) == pytest.approx(0.012)


def test_cached_tokens_are_not_also_charged_as_input() -> None:
    # 500 uncached x 2 + 400 read x 0.1 + 100 written x 2.5 = 1290 per million.
    cost = request_cost_usd(usage(prompt=1000, read=400, write=100), FLAT)
    assert cost == pytest.approx(0.00129)


def test_threshold_is_exclusive_and_long_prompts_cost_more_on_all_tokens() -> None:
    at_threshold = request_cost_usd(usage(prompt=100_000, output=1000), TIERED)
    above = request_cost_usd(usage(prompt=100_001, output=1000), TIERED)
    assert at_threshold == pytest.approx(0.105)
    assert above == pytest.approx(0.525005)


def test_cached_tokens_count_towards_the_threshold() -> None:
    cost = request_cost_usd(usage(prompt=150_000, read=149_000), TIERED)
    assert cost == pytest.approx((1000 * 5.0 + 149_000 * 0.5) / 1_000_000)


def test_each_request_is_priced_alone() -> None:
    # Two 60k prompts: 120k in total, but neither one reaches the long-prompt price.
    cost = requests_cost_usd([usage(prompt=60_000), usage(prompt=60_000)], TIERED)
    assert cost == pytest.approx(0.12)


def test_unknown_price_is_null_not_zero() -> None:
    assert requests_cost_usd([usage(prompt=1000)], None) is None


def test_total_is_unknown_if_any_part_is_unknown() -> None:
    assert total_cost_usd([0.1, 0.2]) == pytest.approx(0.3)
    assert total_cost_usd([0.1, None]) is None
    assert total_cost_usd([]) == 0


def test_sum_usage_adds_every_field() -> None:
    total = sum_usage([usage(1, 2, 3, 4), usage(10, 20, 30, 40)])
    assert total == usage(11, 22, 33, 44)


def test_every_model_the_runner_uses_has_a_price() -> None:
    assert set(MODEL_IDS.values()) <= set(DEFAULT_MODEL_PRICES)


def test_price_table_matches_the_pricing_page_reviewed_on_2026_11_08() -> None:
    sonnet = DEFAULT_MODEL_PRICES["claude-sonnet-5-5"].rates
    assert (sonnet.input, sonnet.output, sonnet.cache_write, sonnet.cache_read) == (
        2.0,
        10.0,
        2.5,
        0.1,
    )
    haiku = DEFAULT_MODEL_PRICES["claude-haiku-5-5"]
    assert (haiku.rates.input, haiku.rates.output) == (0.1, 0.5)
    assert haiku.long_prompt_threshold_tokens == 100_000
    assert haiku.long_prompt_rates is not None
    assert (haiku.long_prompt_rates.input, haiku.long_prompt_rates.output) == (0.5, 2.5)
