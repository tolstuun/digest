"""
Deterministic clustering rules. Pure functions — no DB, no LLM.
"""
from typing import Optional

# Event types that are too vague for clustering.
_UNCLUSTERABLE_EVENT_TYPES = frozenset({"unknown", "other"})


def build_cluster_key(
    event_type: str,
    company_names: list[str],
    amount_text: Optional[str],
    currency: Optional[str],
) -> Optional[str]:
    """
    Build a deterministic cluster key from structured facts.

    Returns None if the facts are insufficient for clustering:
      - event_type is 'unknown' or 'other'
      - no company names provided

    Key format:  "{event_type}:{sorted_companies}[:{amount}][:{currency}]"
    All text is lowercased and stripped before inclusion.
    """
    if event_type in _UNCLUSTERABLE_EVENT_TYPES:
        return None

    companies = sorted(c.lower().strip() for c in company_names if c.strip())
    if not companies:
        return None

    parts = [event_type, "|".join(companies)]
    if amount_text and amount_text.strip():
        parts.append(amount_text.lower().strip())
    if currency and currency.strip():
        parts.append(currency.lower().strip())

    return ":".join(parts)
