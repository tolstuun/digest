"""
Token cost estimation.

Prices are USD per 1M tokens (input / output), keyed by model ID.
Models not in the table get a cost of None (unknown).

Update prices here when Anthropic changes them.
"""
from decimal import Decimal
from typing import Optional

# Prices in USD per 1M tokens: {model_id: (input_price, output_price)}
_PRICES_PER_1M: dict[str, tuple[float, float]] = {
    # Haiku 4.5
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    # Sonnet 4.6
    "claude-sonnet-4-6": (3.00, 15.00),
    # Opus 4.6
    "claude-opus-4-6": (15.00, 75.00),
}


def estimate_cost_usd(
    model_name: str, input_tokens: int, output_tokens: int
) -> Optional[Decimal]:
    """
    Return estimated cost in USD, or None if the model is not in the price table.
    """
    prices = _PRICES_PER_1M.get(model_name)
    if prices is None:
        return None
    input_price, output_price = prices
    cost = (input_tokens * input_price + output_tokens * output_price) / 1_000_000
    return Decimal(str(round(cost, 6)))
