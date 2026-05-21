"""Per-model pricing for cost accounting.

Prices are USD per 1M tokens. Update these as providers change pricing —
ideally pulled from a config service in prod, but a static table is
fine for v1 and matches what every major SDK ships with.

Source: provider public pricing pages (refresh quarterly).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    input_per_1m: float
    output_per_1m: float

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_per_1m / 1_000_000
            + output_tokens * self.output_per_1m / 1_000_000
        )


# Conservative defaults for unknown models — log a warning and use these
# rather than crash the request. Better to over-bill internally than to 500.
UNKNOWN_MODEL_PRICING = ModelPricing(input_per_1m=10.0, output_per_1m=30.0)

PRICING: dict[str, ModelPricing] = {
    # Anthropic
    "claude-opus-4-5": ModelPricing(15.0, 75.0),
    "claude-sonnet-4-5": ModelPricing(3.0, 15.0),
    "claude-haiku-4-5": ModelPricing(1.0, 5.0),
    # OpenAI
    "gpt-4o": ModelPricing(2.5, 10.0),
    "gpt-4o-mini": ModelPricing(0.15, 0.6),
    # xAI Grok
    "grok-3": ModelPricing(3.0, 15.0),
    "grok-3-fast": ModelPricing(5.0, 25.0),
    "grok-3-mini": ModelPricing(0.3, 0.5),
    "grok-3-mini-fast": ModelPricing(0.6, 4.0),
}


def price_for(model: str) -> ModelPricing:
    return PRICING.get(model, UNKNOWN_MODEL_PRICING)
