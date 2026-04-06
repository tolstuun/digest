"""
Anthropic billing/quota error classification.

AnthropicBillingError is raised when the Anthropic API rejects a request
due to insufficient credits, quota exhaustion, or payment failure.

These errors must fail the pipeline run immediately — do not catch and
continue processing remaining items.
"""
from __future__ import annotations

import logging

import anthropic

logger = logging.getLogger(__name__)

# Phrases that signal billing/quota problems in Anthropic error messages
_BILLING_PHRASES = (
    "insufficient_credits",
    "credit balance",
    "billing",
    "payment required",
    "quota",
    "overloaded",
)


class AnthropicBillingError(RuntimeError):
    """
    Raised when an Anthropic API call fails due to billing or quota issues.

    Wraps the original exception as __cause__ so it can be inspected.
    Should propagate through per-item loops (not swallowed) so the pipeline
    run fails fast with a single Telegram alert.
    """

    def __init__(self, message: str, original: Exception) -> None:
        super().__init__(message)
        self.__cause__ = original


def is_billing_quota_error(exc: Exception) -> bool:
    """
    Return True if *exc* looks like an Anthropic billing or quota error.

    Checks:
    - anthropic.AuthenticationError (invalid/suspended API key)
    - HTTP 402 Payment Required
    - HTTP 529 (Anthropic overload / quota)
    - HTTP 429 with billing-related message text
    - Any error message containing known billing phrases
    """
    if isinstance(exc, anthropic.AuthenticationError):
        return True

    if isinstance(exc, anthropic.RateLimitError):
        msg = str(exc).lower()
        return any(phrase in msg for phrase in _BILLING_PHRASES)

    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code in (402, 529):
            return True
        if exc.status_code == 429:
            msg = (exc.message or "").lower()
            return any(phrase in msg for phrase in _BILLING_PHRASES)
        # Check body for billing phrases even on other status codes
        try:
            body = str(exc.body).lower() if exc.body else ""
            if any(phrase in body for phrase in _BILLING_PHRASES):
                return True
        except Exception:  # noqa: BLE001
            pass

    # Fallback: scan the string representation
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _BILLING_PHRASES)


def raise_if_billing_error(exc: Exception) -> None:
    """
    If *exc* is a billing/quota error, re-raise it as AnthropicBillingError.
    Otherwise do nothing (caller handles it normally).
    """
    if is_billing_quota_error(exc):
        raise AnthropicBillingError(
            f"Anthropic billing/quota error: {exc}", original=exc
        ) from exc
