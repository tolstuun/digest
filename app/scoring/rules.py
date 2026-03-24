"""
Deterministic rule-based scoring. Pure functions — no DB, no LLM.

Rule logic is explicit here in code, not hidden inside LLM prompts.
"""

# Base editorial weight per event type.
# Higher = more business-significant for the cybersecurity digest.
_EVENT_TYPE_BASE: dict[str, float] = {
    "mna": 0.90,
    "earnings": 0.85,
    "breach": 0.80,
    "funding": 0.75,
    "regulation": 0.65,
    "executive_change": 0.60,
    "partnership": 0.50,
    "product_launch": 0.45,
    "conference": 0.25,
    "other": 0.10,
    "unknown": 0.05,
}
_DEFAULT_BASE = 0.10


def compute_rule_score(
    event_type: str,
    story_count: int,
    has_amount: bool,
    has_currency: bool,
    max_source_priority: int = 0,
) -> float:
    """
    Compute a deterministic editorial rule score in [0.0, 1.0].

    Bonuses (additive on top of event_type base):
    - Coverage:  +0.05 per additional story beyond the first, capped at +0.15
    - Financial: +0.10 if both amount_text and currency are present; +0.05 if only amount
    - Priority:  +0.05 per 10 units of max_source_priority, capped at +0.10

    All components and weights are explicit in code.
    """
    base = _EVENT_TYPE_BASE.get(event_type, _DEFAULT_BASE)

    # Coverage bonus: each extra story adds signal; diminishing after 3.
    coverage_bonus = min((story_count - 1) * 0.05, 0.15)

    # Financial signal: a concrete amount+currency is high-quality signal.
    if has_amount and has_currency:
        financial_bonus = 0.10
    elif has_amount:
        financial_bonus = 0.05
    else:
        financial_bonus = 0.00

    # Source priority bonus: high-priority sources add slight weight.
    priority_bonus = min(max_source_priority / 200.0, 0.10)

    total = base + coverage_bonus + financial_bonus + priority_bonus
    return round(min(total, 1.0), 4)
