"""Tests for hepagent.token_costs module."""

import pytest

from agents import Usage
from hepagent.token_costs import TOKEN_COSTS_PER_MILLION, calculate_cost


def _make_usage(input_tokens: int = 0, output_tokens: int = 0) -> Usage:
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def test_calculate_cost_exact_match():
    """calculate_cost uses exact model name when available."""
    usage = _make_usage(input_tokens=1_000_000, output_tokens=1_000_000)
    cost = calculate_cost(usage, "openai/gpt-5")
    expected = TOKEN_COSTS_PER_MILLION["openai/gpt-5"]["input"] + TOKEN_COSTS_PER_MILLION["openai/gpt-5"]["output"]
    assert abs(cost - expected) < 1e-9


def test_calculate_cost_partial_match():
    """calculate_cost falls back to substring matching for unknown exact names."""
    usage = _make_usage(input_tokens=1_000_000, output_tokens=1_000_000)
    # "gemini-2.5-flash" should match via substring
    cost = calculate_cost(usage, "some-prefix/gemini-2.5-flash")
    assert cost > 0


def test_calculate_cost_unknown_model_uses_default():
    """calculate_cost uses a conservative default for completely unknown models."""
    usage = _make_usage(input_tokens=1_000_000, output_tokens=1_000_000)
    cost = calculate_cost(usage, "totally-unknown-model-xyz")
    # Default: input=0.250 + output=2.00
    expected = 0.250 + 2.00
    assert abs(cost - expected) < 1e-6


def test_calculate_cost_zero_tokens():
    """calculate_cost returns 0 for zero token usage."""
    usage = _make_usage(0, 0)
    assert calculate_cost(usage, "openai/gpt-5") == 0.0


def test_calculate_cost_scales_with_tokens():
    """Cost scales linearly with token count."""
    usage_small = _make_usage(input_tokens=100_000, output_tokens=0)
    usage_large = _make_usage(input_tokens=1_000_000, output_tokens=0)
    cost_small = calculate_cost(usage_small, "openai/gpt-5")
    cost_large = calculate_cost(usage_large, "openai/gpt-5")
    assert abs(cost_large / cost_small - 10.0) < 1e-6


def test_calculate_cost_empty_model_name():
    """calculate_cost works with an empty model name (uses default costs)."""
    usage = _make_usage(input_tokens=1_000_000, output_tokens=0)
    cost = calculate_cost(usage, "")
    assert cost > 0


def test_calculate_cost_grok():
    """calculate_cost correctly handles xai/grok-4."""
    usage = _make_usage(input_tokens=1_000_000, output_tokens=1_000_000)
    cost = calculate_cost(usage, "xai/grok-4")
    expected = TOKEN_COSTS_PER_MILLION["xai/grok-4"]["input"] + TOKEN_COSTS_PER_MILLION["xai/grok-4"]["output"]
    assert abs(cost - expected) < 1e-9
