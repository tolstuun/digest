"""
Anthropic provider error classification.

Three distinct hard-failure classes:

AnthropicBillingError — true billing/quota/account-balance failures.
  Raised when the API rejects a request due to insufficient credits, quota
  exhaustion, or payment failure.  Triggers a Telegram billing alert and
  immediate pipeline run failure.

AnthropicOverloadedError — transient provider overload (HTTP 529 overloaded_error).
  This is NOT a billing or account issue.  The API is temporarily overloaded.
  write_digest retries with backoff; if still overloaded the pipeline step fails
  clearly as a provider outage, with NO billing/quota alert sent.

WriteDigestPartialOverloadError(AnthropicOverloadedError) — raised by
  write_digest_entries when the overload retry budget is exhausted mid-run.
  Carries partial-state fields (written, skipped, errors, remaining_unwritten)
  so the orchestration layer can surface retryable/partial details in the
  step result while still marking the step and run as failed.
  Already-written entries are committed and safe; a later retry resumes cleanly.

All three must fail the pipeline run — do not swallow them.
"""
from __future__ import annotations

import logging

import anthropic

logger = logging.getLogger(__name__)

# Phrases that signal true billing/quota problems in Anthropic error messages.
# "overloaded" is intentionally excluded — HTTP 529 overloaded_error is transient,
# not a billing issue.
_BILLING_PHRASES = (
    "insufficient_credits",
    "credit balance",
    "billing",
    "payment required",
    "quota",
)


class BatchTimeoutError(Exception):
    """
    Raised when an Anthropic Message Batch does not complete within the
    configured timeout. The pipeline step must be treated as a hard failure —
    there is NO sync fallback.
    """


class AnthropicOverloadedError(RuntimeError):
    """
    Raised when an Anthropic API call returns HTTP 529 with error type
    'overloaded_error'.  This is a transient provider outage, not a billing
    or quota issue.

    write_digest retries with backoff and re-raises this after max attempts,
    failing the step and run without sending a billing/quota Telegram alert.
    """

    def __init__(self, message: str, original: Exception) -> None:
        super().__init__(message)
        self.__cause__ = original


class WriteDigestPartialOverloadError(AnthropicOverloadedError):
    """
    Raised by write_digest_entries when the per-entry overload retry budget is
    exhausted mid-run.

    Already-written entries are committed and will be skipped on the next retry
    (write_digest_entries is idempotent: it skips entries with final_summary set).
    This exception carries enough state for the orchestration layer to produce
    rich, actionable step details without marking the run as silently successful.

    Fields:
      written            — entries successfully written before abort
      skipped            — entries skipped (already had final_summary, or gate failed)
      errors             — entries that failed with a non-overload error
      remaining_unwritten — entries not yet attempted after the aborting entry
    """

    def __init__(
        self,
        message: str,
        original: Exception,
        *,
        written: int,
        skipped: int,
        errors: int,
        remaining_unwritten: int,
    ) -> None:
        super().__init__(message, original=original)
        self.written = written
        self.skipped = skipped
        self.errors = errors
        self.remaining_unwritten = remaining_unwritten


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


def _overloaded_body(body: object) -> bool:
    """Return True if the error body indicates error type 'overloaded_error'."""
    try:
        if hasattr(body, "get"):
            return str(body.get("error", {}).get("type", "")).lower() == "overloaded_error"  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    return False


def is_overloaded_error(exc: Exception) -> bool:
    """
    Return True if *exc* is a transient Anthropic provider overload.

    Specifically: HTTP 529 with error type 'overloaded_error' in the body.
    This is NOT a billing/quota issue and must NOT trigger billing alerts.
    """
    return (
        isinstance(exc, anthropic.APIStatusError)
        and exc.status_code == 529
        and _overloaded_body(exc.body)
    )


def is_billing_quota_error(exc: Exception) -> bool:
    """
    Return True if *exc* looks like an Anthropic billing or quota error.

    Checks:
    - anthropic.AuthenticationError (invalid/suspended API key)
    - HTTP 402 Payment Required
    - HTTP 529 only when the body does NOT indicate 'overloaded_error'
      (overloaded_error is a transient provider outage, not a billing issue)
    - HTTP 429 with billing-related message text
    - Any error message containing known billing phrases
    """
    if isinstance(exc, anthropic.AuthenticationError):
        return True

    if isinstance(exc, anthropic.RateLimitError):
        msg = str(exc).lower()
        return any(phrase in msg for phrase in _BILLING_PHRASES)

    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code == 402:
            return True
        if exc.status_code == 529:
            # Transient overload is NOT a billing error — classify separately
            if _overloaded_body(exc.body):
                return False
            # 529 with billing phrases in body (e.g. quota exhaustion) is billing
            try:
                body = str(exc.body).lower() if exc.body else ""
                if any(phrase in body for phrase in _BILLING_PHRASES):
                    return True
            except Exception:  # noqa: BLE001
                pass
            return False
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


def raise_if_overloaded_error(exc: Exception) -> None:
    """
    If *exc* is a transient Anthropic provider overload, re-raise it as
    AnthropicOverloadedError.  Otherwise do nothing.
    """
    if is_overloaded_error(exc):
        raise AnthropicOverloadedError(
            f"Anthropic provider overloaded (transient): {exc}", original=exc
        ) from exc
