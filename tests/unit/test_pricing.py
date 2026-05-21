"""Unit tests for pricing table."""

from __future__ import annotations

import pytest

from sentinel.providers.pricing import UNKNOWN_MODEL_PRICING, price_for


class TestPricing:
    def test_known_model_returns_its_pricing(self) -> None:
        p = price_for("claude-sonnet-4-5")
        assert p.input_per_1m == 3.0
        assert p.output_per_1m == 15.0

    def test_unknown_model_falls_back(self) -> None:
        p = price_for("imaginary-model-2099")
        assert p is UNKNOWN_MODEL_PRICING

    @pytest.mark.parametrize(
        "model,in_tok,out_tok,expected",
        [
            ("claude-haiku-4-5", 1_000_000, 0, 1.0),
            ("claude-haiku-4-5", 0, 1_000_000, 5.0),
            ("gpt-4o-mini", 1_000_000, 1_000_000, 0.15 + 0.6),
        ],
    )
    def test_cost_math(self, model: str, in_tok: int, out_tok: int, expected: float) -> None:
        assert price_for(model).cost(in_tok, out_tok) == pytest.approx(expected)
