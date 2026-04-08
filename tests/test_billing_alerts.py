"""
Tests for cost visibility and Anthropic billing/quota error handling.

Covers:
  - is_billing_quota_error() classification
  - raise_if_billing_error() converts billing errors to AnthropicBillingError
  - AnthropicBillingError propagates through per-item loops (not swallowed)
  - Pipeline run is failed immediately on billing error (no further steps)
  - Exactly one Telegram alert is sent per billing-failed run
  - run fails billing steps even on the first item (fast-fail)
  - record_usage() stores pipeline_run_id FK
  - get_cost_for_run() returns aggregated cost by stage
"""
import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, call, patch

import anthropic
import pytest

from app.config import (
    AppConfig,
    DatabaseConfig,
    DigestConfig,
    LLMConfig,
    SchedulerConfig,
    Settings,
    TelegramConfig,
)
from app.llm_usage.errors import (
    AnthropicBillingError,
    AnthropicOverloadedError,
    is_billing_quota_error,
    is_overloaded_error,
    raise_if_billing_error,
    raise_if_overloaded_error,
)
from app.llm_usage.schemas import LlmUsageInfo
from app.llm_usage.service import get_cost_for_run, record_usage
from app.models.llm_usage import LlmUsage
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_step import PipelineRunStep
from app.models.raw_item import RawItem
from app.models.source import Source
from app.orchestration.service import run_daily_pipeline

TARGET_DATE = date(2026, 3, 26)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_settings(telegram_enabled: bool = True) -> Settings:
    return Settings(
        config_path="test",
        app=AppConfig(public_base_url="https://example.com"),
        database=DatabaseConfig(),
        llm=LLMConfig(api_key="test-key"),
        telegram=TelegramConfig(
            enabled=telegram_enabled,
            bot_token="tok",
            chat_id="-100",
        ),
        scheduler=SchedulerConfig(
            enabled=False,
            publish_telegram_by_default=False,
        ),
        digest=DigestConfig(output_language="en"),
    )


def _mock_usage() -> LlmUsageInfo:
    return LlmUsageInfo(
        model_name="claude-haiku-4-5-20251001",
        input_tokens=100,
        output_tokens=50,
    )


def _make_source(db) -> Source:
    src = Source(
        name=f"Billing-Test-{uuid.uuid4().hex[:6]}",
        type="rss",
        url=f"https://example.com/{uuid.uuid4().hex[:8]}.rss",
        enabled=True,
    )
    db.add(src)
    db.flush()
    return src


def _make_raw_item(db, source: Source) -> RawItem:
    ri = RawItem(
        source_id=source.id,
        content_hash=uuid.uuid4().hex,
        title="Billing test item",
        url=f"https://example.com/{uuid.uuid4().hex[:8]}",
        raw_payload={"title": "Billing test item"},
    )
    db.add(ri)
    db.flush()
    return ri


# ── is_billing_quota_error() unit tests ──────────────────────────────────────


def test_authentication_error_is_billing():
    exc = anthropic.AuthenticationError(
        message="Invalid API key",
        response=MagicMock(status_code=401, headers={}),
        body={"error": {"type": "authentication_error"}},
    )
    assert is_billing_quota_error(exc) is True


def test_402_status_is_billing():
    exc = anthropic.APIStatusError(
        message="Payment required",
        response=MagicMock(status_code=402, headers={}),
        body={"error": {"message": "insufficient_credits"}},
    )
    assert is_billing_quota_error(exc) is True


def test_529_with_quota_phrase_is_billing():
    """529 whose body contains a billing phrase (quota) is still a billing error."""
    exc = anthropic.APIStatusError(
        message="Quota exceeded",
        response=MagicMock(status_code=529, headers={}),
        body={"error": {"type": "quota_exceeded", "message": "quota exceeded"}},
    )
    assert is_billing_quota_error(exc) is True


def test_529_overloaded_error_is_not_billing():
    """529 overloaded_error is transient — must NOT be classified as billing."""
    exc = anthropic.APIStatusError(
        message="Overloaded",
        response=MagicMock(status_code=529, headers={}),
        body={"error": {"type": "overloaded_error", "message": "Overloaded"}},
    )
    assert is_billing_quota_error(exc) is False


def test_529_overloaded_error_is_detected_as_overloaded():
    """is_overloaded_error returns True for 529 overloaded_error."""
    exc = anthropic.APIStatusError(
        message="Overloaded",
        response=MagicMock(status_code=529, headers={}),
        body={"error": {"type": "overloaded_error", "message": "Overloaded"}},
    )
    assert is_overloaded_error(exc) is True


def test_529_with_billing_body_is_not_overloaded():
    """529 with non-overloaded body is not classified as overloaded."""
    exc = anthropic.APIStatusError(
        message="Quota exceeded",
        response=MagicMock(status_code=529, headers={}),
        body={"error": {"type": "quota_exceeded", "message": "quota exceeded"}},
    )
    assert is_overloaded_error(exc) is False


def test_non_529_is_not_overloaded():
    """Non-529 errors are not overloaded errors."""
    exc = anthropic.APIStatusError(
        message="Internal error",
        response=MagicMock(status_code=500, headers={}),
        body={"error": {"type": "overloaded_error"}},
    )
    assert is_overloaded_error(exc) is False


def test_raise_if_overloaded_error_raises_wrapper():
    """raise_if_overloaded_error converts 529 overloaded to AnthropicOverloadedError."""
    exc = anthropic.APIStatusError(
        message="Overloaded",
        response=MagicMock(status_code=529, headers={}),
        body={"error": {"type": "overloaded_error", "message": "Overloaded"}},
    )
    with pytest.raises(AnthropicOverloadedError):
        raise_if_overloaded_error(exc)


def test_raise_if_overloaded_error_noop_for_billing():
    """raise_if_overloaded_error does nothing for a true billing error."""
    exc = anthropic.AuthenticationError(
        message="Invalid key",
        response=MagicMock(status_code=401, headers={}),
        body={},
    )
    raise_if_overloaded_error(exc)  # must not raise


def test_rate_limit_with_billing_phrase():
    exc = anthropic.RateLimitError(
        message="You have exceeded your credit balance",
        response=MagicMock(status_code=429, headers={}),
        body={"error": {"message": "credit balance exceeded"}},
    )
    assert is_billing_quota_error(exc) is True


def test_generic_rate_limit_not_billing():
    exc = anthropic.RateLimitError(
        message="Too many requests per minute",
        response=MagicMock(status_code=429, headers={}),
        body={"error": {"message": "rate limit exceeded"}},
    )
    assert is_billing_quota_error(exc) is False


def test_500_server_error_not_billing():
    exc = anthropic.APIStatusError(
        message="Internal server error",
        response=MagicMock(status_code=500, headers={}),
        body={"error": {"message": "internal error"}},
    )
    assert is_billing_quota_error(exc) is False


def test_raise_if_billing_error_raises_wrapper():
    exc = anthropic.AuthenticationError(
        message="Invalid API key",
        response=MagicMock(status_code=401, headers={}),
        body={},
    )
    with pytest.raises(AnthropicBillingError):
        raise_if_billing_error(exc)


def test_raise_if_billing_error_does_nothing_for_non_billing():
    exc = ValueError("some other error")
    raise_if_billing_error(exc)  # must not raise


# ── record_usage with pipeline_run_id ─────────────────────────────────────────


def test_record_usage_stores_pipeline_run_id(db):
    run = PipelineRun(
        run_date=TARGET_DATE,
        trigger_type="manual",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    usage = LlmUsageInfo(
        model_name="claude-haiku-4-5-20251001",
        input_tokens=200,
        output_tokens=80,
        related_object_id=str(uuid.uuid4()),
    )
    row = record_usage(db, "extract_facts", usage, pipeline_run_id=run.id)
    db.refresh(row)
    assert row.pipeline_run_id == run.id


def test_record_usage_without_pipeline_run_id(db):
    usage = LlmUsageInfo(
        model_name="claude-haiku-4-5-20251001",
        input_tokens=100,
        output_tokens=40,
    )
    row = record_usage(db, "assess", usage)
    db.refresh(row)
    assert row.pipeline_run_id is None


# ── get_cost_for_run() ─────────────────────────────────────────────────────────


def test_get_cost_for_run_aggregates_by_stage(db):
    run = PipelineRun(
        run_date=TARGET_DATE,
        trigger_type="manual",
        status="success",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    # Two extract_facts usages and one assess usage
    for _ in range(2):
        usage = LlmUsageInfo(
            model_name="claude-haiku-4-5-20251001",
            input_tokens=100,
            output_tokens=50,
        )
        record_usage(db, "extract_facts", usage, pipeline_run_id=run.id)

    assess_usage = LlmUsageInfo(
        model_name="claude-haiku-4-5-20251001",
        input_tokens=50,
        output_tokens=25,
    )
    record_usage(db, "assess", assess_usage, pipeline_run_id=run.id)

    cost_info = get_cost_for_run(db, run)

    assert "total_cost_usd" in cost_info
    assert "stages" in cost_info
    assert "extract_facts" in cost_info["stages"]
    assert "assess" in cost_info["stages"]
    assert cost_info["total_cost_usd"] >= 0


def test_get_cost_for_run_excludes_other_runs(db):
    run1 = PipelineRun(
        run_date=TARGET_DATE,
        trigger_type="manual",
        status="success",
        started_at=datetime.now(timezone.utc),
    )
    run2 = PipelineRun(
        run_date=TARGET_DATE,
        trigger_type="manual",
        status="success",
        started_at=datetime.now(timezone.utc),
    )
    db.add_all([run1, run2])
    db.flush()

    usage = LlmUsageInfo(
        model_name="claude-haiku-4-5-20251001",
        input_tokens=100,
        output_tokens=50,
    )
    record_usage(db, "extract_facts", usage, pipeline_run_id=run1.id)

    cost_run2 = get_cost_for_run(db, run2)
    assert cost_run2["total_cost_usd"] == 0.0
    assert cost_run2["stages"] == {}


# ── pipeline billing fast-fail behavior ───────────────────────────────────────


def _run_pipeline_with_billing_error_on_step(db, step_to_fail: str):
    """
    Helper: run the pipeline where the LLM call raises AnthropicBillingError
    starting at step_to_fail. Returns (summary, telegram_mock).
    """
    billing_exc = AnthropicBillingError(
        "Anthropic billing/quota error: Payment required",
        original=Exception("Payment required"),
    )

    cfg = _make_settings(telegram_enabled=True)

    extract_mock = MagicMock(side_effect=billing_exc) if step_to_fail == "extract_facts" \
        else MagicMock(return_value={"total": 0, "new": 0, "updated": 0, "errors": 0})
    assess_mock = MagicMock(side_effect=billing_exc) if step_to_fail == "assess" \
        else MagicMock(return_value={"total": 0, "assessed": 0, "skipped": 0, "errors": 0})

    tg_mock = MagicMock()

    with patch.multiple(
        "app.orchestration.service",
        _run_ingest=MagicMock(return_value={"new": 0, "skipped": 0, "errors": 0, "sources": 0}),
        _run_normalize=MagicMock(return_value={"total": 0, "new": 0, "skipped": 0}),
        _run_extract_facts=extract_mock,
        _run_cluster_event=MagicMock(return_value={"total": 0, "clustered": 0, "not_clustered": 0}),
        _run_assess=assess_mock,
        _run_assemble_digest=MagicMock(return_value={"digest_run_id": str(uuid.uuid4()), "total_included": 0, "created": True, "sections": []}),
        _run_write_digest=MagicMock(return_value={"total": 0, "written": 0, "skipped": 0, "errors": 0}),
        _run_render_digest=MagicMock(return_value={"digest_page_id": str(uuid.uuid4()), "slug": "test", "created": True}),
        _run_publish_telegram=MagicMock(return_value={"skipped": True, "reason": "test"}),
        send_operational_alert=tg_mock,
    ):
        summary = run_daily_pipeline(db, TARGET_DATE, cfg=cfg)

    return summary, tg_mock


def test_billing_error_fails_run_immediately(db):
    """Pipeline run is marked failed when billing error occurs."""
    summary, _ = _run_pipeline_with_billing_error_on_step(db, "extract_facts")
    assert summary["status"] == "failed"
    assert summary["failed_step"] == "extract_facts"


def test_billing_error_no_further_steps(db):
    """Steps after billing failure are NOT executed."""
    billing_exc = AnthropicBillingError("billing", original=Exception("billing"))
    assess_mock = MagicMock()

    with patch.multiple(
        "app.orchestration.service",
        _run_ingest=MagicMock(return_value={"new": 0, "skipped": 0, "errors": 0, "sources": 0}),
        _run_normalize=MagicMock(return_value={"total": 0, "new": 0, "skipped": 0}),
        _run_extract_facts=MagicMock(side_effect=billing_exc),
        _run_cluster_event=MagicMock(return_value={"total": 0, "clustered": 0, "not_clustered": 0}),
        _run_assess=assess_mock,
        _run_assemble_digest=MagicMock(return_value={"digest_run_id": str(uuid.uuid4()), "total_included": 0, "created": True, "sections": []}),
        _run_write_digest=MagicMock(return_value={"total": 0, "written": 0, "skipped": 0, "errors": 0}),
        _run_render_digest=MagicMock(return_value={"digest_page_id": str(uuid.uuid4()), "slug": "test", "created": True}),
        _run_publish_telegram=MagicMock(return_value={"skipped": True}),
        send_operational_alert=MagicMock(),
    ):
        run_daily_pipeline(db, TARGET_DATE, cfg=_make_settings())

    # assess must NOT have been called because pipeline stopped at extract_facts
    assess_mock.assert_not_called()


def test_billing_error_sends_exactly_one_telegram_alert(db):
    """Exactly one Telegram alert is sent per billing-failed run."""
    summary, tg_mock = _run_pipeline_with_billing_error_on_step(db, "extract_facts")
    assert summary["status"] == "failed"
    assert tg_mock.call_count == 1


def test_billing_error_no_telegram_when_disabled(db):
    """No Telegram alert when telegram is disabled."""
    billing_exc = AnthropicBillingError("billing", original=Exception("billing"))
    cfg = _make_settings(telegram_enabled=False)
    tg_mock = MagicMock()

    with patch.multiple(
        "app.orchestration.service",
        _run_ingest=MagicMock(return_value={"new": 0, "skipped": 0, "errors": 0, "sources": 0}),
        _run_normalize=MagicMock(return_value={"total": 0, "new": 0, "skipped": 0}),
        _run_extract_facts=MagicMock(side_effect=billing_exc),
        _run_cluster_event=MagicMock(return_value={"total": 0, "clustered": 0, "not_clustered": 0}),
        _run_assess=MagicMock(return_value={"total": 0, "assessed": 0, "skipped": 0, "errors": 0}),
        _run_assemble_digest=MagicMock(return_value={"digest_run_id": str(uuid.uuid4()), "total_included": 0, "created": True, "sections": []}),
        _run_write_digest=MagicMock(return_value={"total": 0, "written": 0, "skipped": 0, "errors": 0}),
        _run_render_digest=MagicMock(return_value={"digest_page_id": str(uuid.uuid4()), "slug": "test", "created": True}),
        _run_publish_telegram=MagicMock(return_value={"skipped": True}),
        send_operational_alert=tg_mock,
    ):
        run_daily_pipeline(db, TARGET_DATE, cfg=cfg)

    tg_mock.assert_not_called()


def test_pipeline_run_step_marked_failed_on_billing(db):
    """The failed step row is persisted in pipeline_run_steps with status=failed."""
    summary, _ = _run_pipeline_with_billing_error_on_step(db, "extract_facts")

    run_id = uuid.UUID(summary["pipeline_run_id"])
    failed_steps = (
        db.query(PipelineRunStep)
        .filter_by(pipeline_run_id=run_id, status="failed")
        .all()
    )
    assert len(failed_steps) == 1
    assert failed_steps[0].step_name == "extract_facts"
    assert "AnthropicBillingError" in (failed_steps[0].error_message or "")


def test_pipeline_run_error_message_contains_billing_class(db):
    """PipelineRun.error_message contains 'AnthropicBillingError' for easy UI detection."""
    summary, _ = _run_pipeline_with_billing_error_on_step(db, "extract_facts")

    run_id = uuid.UUID(summary["pipeline_run_id"])
    run = db.get(PipelineRun, run_id)
    assert run is not None
    assert "AnthropicBillingError" in (run.error_message or "")


# ── AnthropicOverloadedError: pipeline behavior ───────────────────────────────


def _run_pipeline_with_overloaded_on_write_digest(db) -> tuple[dict, MagicMock]:
    """
    Helper: run the pipeline where write_digest raises AnthropicOverloadedError.
    Returns (summary, telegram_mock).
    """
    overloaded_exc = AnthropicOverloadedError(
        "Anthropic provider overloaded (transient): Overloaded",
        original=Exception("Overloaded"),
    )
    cfg = _make_settings(telegram_enabled=True)
    tg_mock = MagicMock()

    with patch.multiple(
        "app.orchestration.service",
        _run_ingest=MagicMock(return_value={"new": 0, "skipped": 0, "errors": 0, "sources": 0}),
        _run_normalize=MagicMock(return_value={"total": 0, "new": 0, "skipped": 0}),
        _run_extract_facts=MagicMock(return_value={"eligible": 0, "processed": 0}),
        _run_cluster_event=MagicMock(return_value={"total": 0, "clustered": 0, "not_clustered": 0}),
        _run_assess=MagicMock(return_value={"eligible": 0, "assessed": 0}),
        _run_assemble_digest=MagicMock(return_value={"digest_run_id": str(uuid.uuid4()), "total_included": 0, "created": True, "sections": []}),
        _run_write_digest=MagicMock(side_effect=overloaded_exc),
        _run_render_digest=MagicMock(return_value={"digest_page_id": str(uuid.uuid4()), "slug": "test", "created": True}),
        _run_publish_telegram=MagicMock(return_value={"skipped": True}),
        send_operational_alert=tg_mock,
    ):
        summary = run_daily_pipeline(db, TARGET_DATE, cfg=cfg)

    return summary, tg_mock


def test_overloaded_error_fails_pipeline_run(db):
    """AnthropicOverloadedError marks the pipeline run as failed."""
    summary, _ = _run_pipeline_with_overloaded_on_write_digest(db)
    assert summary["status"] == "failed"
    assert summary["failed_step"] == "write_digest"


def test_overloaded_error_no_telegram_alert(db):
    """No billing/quota Telegram alert is sent for transient provider overload."""
    _, tg_mock = _run_pipeline_with_overloaded_on_write_digest(db)
    tg_mock.assert_not_called()


def test_overloaded_error_step_marked_failed(db):
    """The write_digest step row is persisted as failed with AnthropicOverloadedError."""
    summary, _ = _run_pipeline_with_overloaded_on_write_digest(db)
    run_id = uuid.UUID(summary["pipeline_run_id"])
    failed_steps = (
        db.query(PipelineRunStep)
        .filter_by(pipeline_run_id=run_id, status="failed")
        .all()
    )
    assert len(failed_steps) == 1
    assert failed_steps[0].step_name == "write_digest"
    assert "AnthropicOverloadedError" in (failed_steps[0].error_message or "")


def test_overloaded_error_run_error_contains_overloaded_class(db):
    """PipelineRun.error_message contains 'AnthropicOverloadedError', not 'AnthropicBillingError'."""
    summary, _ = _run_pipeline_with_overloaded_on_write_digest(db)
    run_id = uuid.UUID(summary["pipeline_run_id"])
    run = db.get(PipelineRun, run_id)
    assert "AnthropicOverloadedError" in (run.error_message or "")
    assert "AnthropicBillingError" not in (run.error_message or "")


def test_overloaded_billing_alert_not_sent_even_when_telegram_enabled(db):
    """Billing alert is NOT sent for overloaded error even if telegram.enabled=True."""
    summary, tg_mock = _run_pipeline_with_overloaded_on_write_digest(db)
    assert summary["status"] == "failed"
    tg_mock.assert_not_called()
