"""
Tests for Anthropic Message Batches assess mode (editorial scoring).

All Anthropic API calls are mocked — no real network requests.
Tests cover:
  - batch request structure (custom_id = cluster_id, one request per cluster)
  - language-aware schema reused from sync path (EN/RU)
  - result mapping via custom_id → EventClusterAssessment persisted
  - LLM usage recorded with real token counts and pipeline_run_id
  - errored items produce no LlmUsage rows (no phantom zero-cost rows)
  - BatchTimeoutError raised on timeout; results() never called; no sync fallback
  - orchestration branching (_run_assess dispatches batch vs sync)
  - all required observability fields present in step details
  - skipped_gate and capped counted before submission
"""
import hashlib
import time
import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.config import (
    AppConfig, DatabaseConfig, DigestConfig, LLMConfig,
    SchedulerConfig, Settings, TelegramConfig, load_settings,
)
from app.llm_usage.errors import BatchTimeoutError
from app.llm_usage.schemas import LlmUsageInfo
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.llm_usage import LlmUsage
from app.models.pipeline_run import PipelineRun
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts
from app.scoring.batch import BatchItemResult, assess_cluster_batch
from app.scoring.schemas import ClusterAssessment

RUN_DATE = date(2026, 3, 25)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_settings(
    use_batch_assess: bool = False,
    batch_timeout_minutes: int = 90,
    batch_poll_interval_seconds: int = 30,
    output_language: str = "en",
) -> Settings:
    return Settings(
        config_path="test",
        app=AppConfig(public_base_url="https://example.com"),
        database=DatabaseConfig(),
        llm=LLMConfig(
            api_key="test-key",
            model_scoring="claude-haiku-4-5-20251001",
            use_batch_assess=use_batch_assess,
            batch_poll_interval_seconds=batch_poll_interval_seconds,
            batch_timeout_minutes=batch_timeout_minutes,
        ),
        telegram=TelegramConfig(),
        scheduler=SchedulerConfig(),
        digest=DigestConfig(output_language=output_language),
    )


def _valid_assessment_input() -> dict:
    return dict(
        primary_section="companies_business",
        llm_score=0.85,
        include_in_digest=True,
        why_it_matters_en="Significant deal.",
        why_it_matters_ru="",
        editorial_notes="",
    )


def _make_mock_batch(batch_id: str = "batch_assess001", status: str = "ended") -> MagicMock:
    batch = MagicMock()
    batch.id = batch_id
    batch.processing_status = status
    return batch


def _make_succeeded_result_item(
    cluster_id: str,
    tool_input: dict,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> MagicMock:
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = tool_input

    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens

    message = MagicMock()
    message.content = [tool_block]
    message.usage = usage

    result = MagicMock()
    result.type = "succeeded"
    result.message = message

    item = MagicMock()
    item.custom_id = cluster_id
    item.result = result
    return item


def _make_errored_result_item(cluster_id: str, error_type: str = "errored") -> MagicMock:
    result = MagicMock()
    result.type = error_type
    result.error = f"{error_type} error detail"

    item = MagicMock()
    item.custom_id = cluster_id
    item.result = result
    return item


def _make_cluster_input(cluster_id: str = None):
    """Return (cluster_id_str, ClusterInput) for unit tests that don't need DB."""
    from app.scoring.schemas import ClusterInput
    cid = cluster_id or str(uuid.uuid4())
    return cid, ClusterInput(
        cluster_id=cid,
        event_type="funding",
        story_count=1,
        company_names=["Acme Corp"],
        amount_text="$50M",
        currency="USD",
        canonical_summary_en="Acme raised $50M.",
        canonical_summary_ru=None,
        representative_title="Acme Corp raises $50M",
    )


def _make_db_cluster(
    db,
    *,
    published_at: datetime | None = None,
    event_type: str = "funding",
    company_suffix: str = "",
) -> EventCluster:
    """Create Source → RawItem → Story → StoryFacts → EventCluster chain.

    Title and summary include "cybersecurity" so cluster_passes_any_section_gate
    returns True in orchestration tests without needing to mock the gate.
    """
    src = Source(
        name=f"Feed-{uuid.uuid4().hex[:8]}",
        type="rss",
        url=f"https://example.com/feed/{uuid.uuid4().hex[:8]}",
        enabled=True,
    )
    db.add(src)
    db.flush()

    title = f"Acme{company_suffix} cybersecurity raises $50M"
    summary = f"Acme{company_suffix} cybersecurity funding round of $50M announced."
    ri = RawItem(
        source_id=src.id,
        content_hash=hashlib.sha256(f"{title}{uuid.uuid4()}".encode()).hexdigest(),
        title=title,
        url=f"https://example.com/{uuid.uuid4().hex[:8]}",
        raw_payload={"title": title, "summary": summary},
    )
    db.add(ri)
    db.flush()

    story = Story(
        raw_item_id=ri.id,
        source_id=src.id,
        title=title,
        url=ri.url,
        canonical_url=ri.url,
        published_at=published_at,
    )
    db.add(story)
    db.flush()

    facts = StoryFacts(
        story_id=story.id,
        model_name="claude-haiku-4-5-20251001",
        event_type=event_type,
        company_names=[f"Acme{company_suffix} Corp"],
        person_names=[],
        product_names=[],
        geography_names=[],
        amount_text="$50M",
        currency="USD",
        source_language="en",
        canonical_summary_en=summary,
        canonical_summary_ru="",
        extraction_confidence=0.9,
    )
    db.add(facts)
    db.flush()

    cluster = EventCluster(
        cluster_key=f"funding-acme{company_suffix}-50m-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        representative_story_id=story.id,
    )
    db.add(cluster)
    db.flush()

    story.event_cluster_id = cluster.id
    db.commit()
    db.refresh(cluster)
    return cluster


# ── assess_cluster_batch: request structure ───────────────────────────────────

def test_batch_assess_uses_cluster_id_as_custom_id():
    """Each request's custom_id must equal the cluster_id string."""
    cid, cluster_input = _make_cluster_input()
    mock_batch = _make_mock_batch()

    with patch("app.scoring.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = []

        assess_cluster_batch(
            cluster_inputs=[(cid, cluster_input)],
            api_key="key",
            model="claude-haiku-4-5-20251001",
            output_language="en",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    create_call = client.messages.batches.create.call_args
    requests = create_call.kwargs["requests"]
    assert len(requests) == 1
    assert requests[0]["custom_id"] == cid


def test_batch_assess_one_request_per_cluster():
    """Multiple clusters → one request per cluster, all custom_ids present."""
    inputs = [_make_cluster_input() for _ in range(3)]
    expected_ids = {cid for cid, _ in inputs}
    mock_batch = _make_mock_batch()

    with patch("app.scoring.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = []

        assess_cluster_batch(
            cluster_inputs=inputs,
            api_key="key",
            model="claude-haiku-4-5-20251001",
            output_language="en",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    create_call = client.messages.batches.create.call_args
    actual_ids = {r["custom_id"] for r in create_call.kwargs["requests"]}
    assert actual_ids == expected_ids


def test_batch_assess_schema_en_requires_en_why_not_ru():
    """EN mode: why_it_matters_en in required; why_it_matters_ru not required."""
    cid, cluster_input = _make_cluster_input()
    mock_batch = _make_mock_batch()

    with patch("app.scoring.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = []

        assess_cluster_batch(
            cluster_inputs=[(cid, cluster_input)],
            api_key="key",
            model="model",
            output_language="en",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    req = client.messages.batches.create.call_args.kwargs["requests"][0]
    required = req["params"]["tools"][0]["input_schema"]["required"]
    assert "why_it_matters_en" in required
    assert "why_it_matters_ru" not in required


def test_batch_assess_schema_ru_requires_ru_why_not_en():
    """RU mode: why_it_matters_ru in required; why_it_matters_en not required."""
    cid, cluster_input = _make_cluster_input()
    mock_batch = _make_mock_batch()

    with patch("app.scoring.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = []

        assess_cluster_batch(
            cluster_inputs=[(cid, cluster_input)],
            api_key="key",
            model="model",
            output_language="ru",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    req = client.messages.batches.create.call_args.kwargs["requests"][0]
    required = req["params"]["tools"][0]["input_schema"]["required"]
    assert "why_it_matters_ru" in required
    assert "why_it_matters_en" not in required


# ── assess_cluster_batch: result parsing ──────────────────────────────────────

def test_batch_assess_succeeded_result_parsed():
    """A succeeded result is parsed into ClusterAssessment with correct usage."""
    cid, cluster_input = _make_cluster_input()
    tool_input = _valid_assessment_input()
    mock_batch = _make_mock_batch()
    result_item = _make_succeeded_result_item(cid, tool_input, input_tokens=120, output_tokens=60)

    with patch("app.scoring.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = [result_item]

        _, results, _ = assess_cluster_batch(
            cluster_inputs=[(cid, cluster_input)],
            api_key="key",
            model="claude-haiku-4-5-20251001",
            output_language="en",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    assert len(results) == 1
    item = results[0]
    assert item.cluster_id == cid
    assert item.error is None
    assert item.result is not None
    assert item.result.primary_section == "companies_business"
    assert item.result.llm_score == 0.85
    assert item.usage is not None
    assert item.usage.input_tokens == 120
    assert item.usage.output_tokens == 60
    assert item.usage.related_object_id == cid


def test_batch_assess_errored_result_has_no_usage():
    """Errored batch items: result=None, usage=None — no phantom zero-cost rows."""
    cid, cluster_input = _make_cluster_input()
    mock_batch = _make_mock_batch()
    result_item = _make_errored_result_item(cid, error_type="errored")

    with patch("app.scoring.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = [result_item]

        _, results, _ = assess_cluster_batch(
            cluster_inputs=[(cid, cluster_input)],
            api_key="key",
            model="model",
            output_language="en",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    assert len(results) == 1
    item = results[0]
    assert item.result is None
    assert item.usage is None
    assert item.error is not None
    assert "errored" in item.error


def test_batch_assess_returns_batch_id_and_poll_duration():
    """Return tuple carries batch_id string and non-negative poll_duration_seconds."""
    cid, cluster_input = _make_cluster_input()
    mock_batch = _make_mock_batch(batch_id="msgbatch_xyz789")

    with patch("app.scoring.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = []

        batch_id, _, poll_duration = assess_cluster_batch(
            cluster_inputs=[(cid, cluster_input)],
            api_key="key",
            model="model",
            output_language="en",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    assert batch_id == "msgbatch_xyz789"
    assert isinstance(poll_duration, float)
    assert poll_duration >= 0


# ── assess_cluster_batch: timeout behavior ────────────────────────────────────

def _make_monotonic_that_times_out():
    """Return a monotonic() side_effect that reports timeout after first poll."""
    calls = {"n": 0}

    def monotonic():
        calls["n"] += 1
        if calls["n"] <= 2:
            return 0.0
        return 999999.0

    return monotonic


def test_batch_assess_timeout_raises_batch_timeout_error():
    """When batch never reaches 'ended', BatchTimeoutError is raised."""
    cid, cluster_input = _make_cluster_input()
    in_progress = _make_mock_batch(status="in_progress")

    with (
        patch("app.scoring.batch.anthropic.Anthropic") as MockClient,
        patch("app.scoring.batch.time.monotonic", side_effect=_make_monotonic_that_times_out()),
        patch("app.scoring.batch.time.sleep"),
    ):
        client = MockClient.return_value
        client.messages.batches.create.return_value = in_progress
        client.messages.batches.retrieve.return_value = in_progress

        with pytest.raises(BatchTimeoutError) as exc_info:
            assess_cluster_batch(
                cluster_inputs=[(cid, cluster_input)],
                api_key="key",
                model="model",
                output_language="en",
                poll_interval_seconds=30,
                timeout_minutes=90,
            )

    assert "did not complete within" in str(exc_info.value)
    assert "90 minutes" in str(exc_info.value)


def test_batch_assess_timeout_results_never_called():
    """On timeout, results() is never called — no partial-cost surprise."""
    cid, cluster_input = _make_cluster_input()
    in_progress = _make_mock_batch(status="in_progress")

    with (
        patch("app.scoring.batch.anthropic.Anthropic") as MockClient,
        patch("app.scoring.batch.time.monotonic", side_effect=_make_monotonic_that_times_out()),
        patch("app.scoring.batch.time.sleep"),
    ):
        client = MockClient.return_value
        client.messages.batches.create.return_value = in_progress
        client.messages.batches.retrieve.return_value = in_progress

        with pytest.raises(BatchTimeoutError):
            assess_cluster_batch(
                cluster_inputs=[(cid, cluster_input)],
                api_key="key",
                model="model",
                output_language="en",
                poll_interval_seconds=30,
                timeout_minutes=90,
            )

    client.messages.batches.results.assert_not_called()


def test_batch_assess_polls_until_ended():
    """Polls repeatedly until 'ended'; sleep called between polls."""
    cid, cluster_input = _make_cluster_input()
    in_progress = _make_mock_batch(status="in_progress")
    ended = _make_mock_batch(status="ended")

    with (
        patch("app.scoring.batch.anthropic.Anthropic") as MockClient,
        patch("app.scoring.batch.time.sleep") as mock_sleep,
    ):
        client = MockClient.return_value
        client.messages.batches.create.return_value = in_progress
        client.messages.batches.retrieve.side_effect = [in_progress, ended]
        client.messages.batches.results.return_value = []

        assess_cluster_batch(
            cluster_inputs=[(cid, cluster_input)],
            api_key="key",
            model="model",
            output_language="en",
            poll_interval_seconds=30,
            timeout_minutes=90,
        )

    assert client.messages.batches.retrieve.call_count == 2
    mock_sleep.assert_called_once()


# ── assess_cluster_batch_run: DB integration ──────────────────────────────────

def test_batch_assess_run_persists_assessment_for_succeeded_items(db):
    """EventClusterAssessment is created for each succeeded batch item."""
    cluster = _make_db_cluster(db)
    cluster_id = str(cluster.id)
    cfg = _make_settings(use_batch_assess=True)

    mock_batch_result = (
        "batch_abc",
        [BatchItemResult(
            cluster_id=cluster_id,
            result=ClusterAssessment(**_valid_assessment_input()),
            usage=LlmUsageInfo(
                model_name="claude-haiku-4-5-20251001",
                input_tokens=100,
                output_tokens=50,
                related_object_id=cluster_id,
            ),
            error=None,
        )],
        1.5,
    )

    from app.scoring.service import assess_cluster_batch_run
    with patch("app.scoring.batch.assess_cluster_batch", return_value=mock_batch_result):
        result = assess_cluster_batch_run(db, [cluster], cfg)

    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assessment = db.query(EventClusterAssessment).filter_by(event_cluster_id=cluster.id).first()
    assert assessment is not None
    assert assessment.primary_section == "companies_business"
    assert assessment.llm_score == 0.85
    assert assessment.include_in_digest is True
    # Score formula must match sync path: 0.4 * rule_score + 0.6 * llm_score
    assert assessment.final_score is not None


def test_batch_assess_run_score_formula_matches_sync_path(db):
    """final_score = 0.4 * rule_score + 0.6 * llm_score — identical to sync path."""
    from app.scoring.rules import compute_rule_score

    cluster = _make_db_cluster(db)
    cluster_id = str(cluster.id)
    cfg = _make_settings(use_batch_assess=True)

    llm_score = 0.7
    mock_batch_result = (
        "batch_score",
        [BatchItemResult(
            cluster_id=cluster_id,
            result=ClusterAssessment(**{**_valid_assessment_input(), "llm_score": llm_score}),
            usage=LlmUsageInfo(model_name="test", input_tokens=10, output_tokens=5),
            error=None,
        )],
        1.0,
    )

    from app.scoring.service import assess_cluster_batch_run, _build_cluster_input
    _, rule_score = _build_cluster_input(db, cluster)
    expected_final = round(0.4 * rule_score + 0.6 * llm_score, 4)

    with patch("app.scoring.batch.assess_cluster_batch", return_value=mock_batch_result):
        assess_cluster_batch_run(db, [cluster], cfg)

    assessment = db.query(EventClusterAssessment).filter_by(event_cluster_id=cluster.id).first()
    assert assessment.final_score == expected_final


def test_batch_assess_run_records_usage_with_pipeline_run_id(db):
    """Each succeeded item produces one LlmUsage row with real token counts and pipeline_run_id."""
    cluster = _make_db_cluster(db)
    cluster_id = str(cluster.id)
    cfg = _make_settings(use_batch_assess=True)

    run = PipelineRun(
        run_date=RUN_DATE,
        trigger_type="manual",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    run_id = run.id

    mock_batch_result = (
        "batch_usage",
        [BatchItemResult(
            cluster_id=cluster_id,
            result=ClusterAssessment(**_valid_assessment_input()),
            usage=LlmUsageInfo(
                model_name="claude-haiku-4-5-20251001",
                input_tokens=123,
                output_tokens=67,
                related_object_id=cluster_id,
            ),
            error=None,
        )],
        2.0,
    )

    from app.scoring.service import assess_cluster_batch_run
    with patch("app.scoring.batch.assess_cluster_batch", return_value=mock_batch_result):
        assess_cluster_batch_run(db, [cluster], cfg, pipeline_run_id=run_id)

    usage_row = db.query(LlmUsage).filter_by(pipeline_run_id=run_id).first()
    assert usage_row is not None
    assert usage_row.input_tokens == 123
    assert usage_row.output_tokens == 67
    assert usage_row.stage_name == "assess"


def test_batch_assess_run_no_usage_row_for_failed_items(db):
    """Errored batch items produce no LlmUsage rows — no phantom zero-cost records."""
    cluster = _make_db_cluster(db)
    cluster_id = str(cluster.id)
    cfg = _make_settings(use_batch_assess=True)
    run_id = uuid.uuid4()

    mock_batch_result = (
        "batch_err",
        [BatchItemResult(
            cluster_id=cluster_id,
            result=None,
            usage=None,
            error="errored: some API error",
        )],
        1.0,
    )

    from app.scoring.service import assess_cluster_batch_run
    with patch("app.scoring.batch.assess_cluster_batch", return_value=mock_batch_result):
        result = assess_cluster_batch_run(db, [cluster], cfg, pipeline_run_id=run_id)

    assert result["failed"] == 1
    assert result["succeeded"] == 0
    usage_count = db.query(LlmUsage).filter_by(pipeline_run_id=run_id).count()
    assert usage_count == 0
    # No assessment row created for failed item
    assessment = db.query(EventClusterAssessment).filter_by(event_cluster_id=cluster.id).first()
    assert assessment is None


def test_batch_assess_run_result_has_required_observability_fields(db):
    """Return dict contains all required observability fields."""
    cluster = _make_db_cluster(db)
    cluster_id = str(cluster.id)
    cfg = _make_settings(use_batch_assess=True)

    mock_batch_result = (
        "msgbatch_obs001",
        [BatchItemResult(
            cluster_id=cluster_id,
            result=ClusterAssessment(**_valid_assessment_input()),
            usage=LlmUsageInfo(model_name="test", input_tokens=10, output_tokens=5),
            error=None,
        )],
        3.7,
    )

    from app.scoring.service import assess_cluster_batch_run
    with patch("app.scoring.batch.assess_cluster_batch", return_value=mock_batch_result):
        result = assess_cluster_batch_run(db, [cluster], cfg)

    assert result["mode"] == "batch"
    assert result["batch_id"] == "msgbatch_obs001"
    assert result["submitted"] == 1
    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert result["timed_out"] is False
    assert result["poll_duration_seconds"] == 3.7


def test_batch_assess_run_idempotent_on_re_assess(db):
    """Re-assessing a cluster that already has an assessment updates the row (upsert)."""
    cluster = _make_db_cluster(db)
    cluster_id = str(cluster.id)
    cfg = _make_settings(use_batch_assess=True)

    existing = EventClusterAssessment(
        event_cluster_id=cluster.id,
        primary_section="other",
        llm_score=0.1,
        rule_score=0.1,
        final_score=0.1,
        include_in_digest=False,
    )
    db.add(existing)
    db.commit()

    mock_batch_result = (
        "batch_re",
        [BatchItemResult(
            cluster_id=cluster_id,
            result=ClusterAssessment(**_valid_assessment_input()),
            usage=LlmUsageInfo(model_name="test", input_tokens=10, output_tokens=5),
            error=None,
        )],
        1.0,
    )

    from app.scoring.service import assess_cluster_batch_run
    with patch("app.scoring.batch.assess_cluster_batch", return_value=mock_batch_result):
        assess_cluster_batch_run(db, [cluster], cfg)

    # Still one row — updated, not duplicated
    count = db.query(EventClusterAssessment).filter_by(event_cluster_id=cluster.id).count()
    assert count == 1
    updated = db.query(EventClusterAssessment).filter_by(event_cluster_id=cluster.id).first()
    assert updated.primary_section == "companies_business"
    assert updated.llm_score == 0.85


# ── _run_assess: orchestration branching ──────────────────────────────────────

def test_run_assess_uses_batch_when_flag_set(db):
    """_run_assess calls assess_cluster_batch_run when use_batch_assess=True."""
    from app.orchestration.service import _run_assess

    pub = datetime(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day, 12, tzinfo=timezone.utc)
    cluster = _make_db_cluster(db, published_at=pub)
    cfg = _make_settings(use_batch_assess=True)

    batch_return = {
        "mode": "batch", "batch_id": "b1", "submitted": 1,
        "succeeded": 1, "failed": 0, "timed_out": False,
        "poll_duration_seconds": 1.0,
    }

    with patch("app.orchestration.service.assess_cluster_batch_run", return_value=batch_return) as mock_batch, \
         patch("app.orchestration.service.assess_cluster") as mock_sync:
        result = _run_assess(db, RUN_DATE, max_assess=10, cfg=cfg)

    mock_batch.assert_called_once()
    mock_sync.assert_not_called()
    assert result["mode"] == "batch"
    assert result["eligible"] == 1


def test_run_assess_uses_sync_when_flag_false(db):
    """_run_assess uses sync per-cluster loop when use_batch_assess=False."""
    from app.orchestration.service import _run_assess

    pub = datetime(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day, 12, tzinfo=timezone.utc)
    cluster = _make_db_cluster(db, published_at=pub)
    cfg = _make_settings(use_batch_assess=False)

    mock_assessment = EventClusterAssessment(event_cluster_id=cluster.id)

    with patch("app.orchestration.service.assess_cluster_batch_run") as mock_batch, \
         patch("app.orchestration.service.assess_cluster", return_value=(mock_assessment, True)) as mock_sync:
        result = _run_assess(db, RUN_DATE, max_assess=10, cfg=cfg)

    mock_batch.assert_not_called()
    mock_sync.assert_called_once()
    assert "processed" in result
    assert "mode" not in result


def test_run_assess_uses_sync_when_cfg_is_none(db):
    """_run_assess falls back to sync when cfg=None (default, safe)."""
    from app.orchestration.service import _run_assess

    pub = datetime(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day, 12, tzinfo=timezone.utc)
    _make_db_cluster(db, published_at=pub)
    mock_assessment = MagicMock()

    with patch("app.orchestration.service.assess_cluster_batch_run") as mock_batch, \
         patch("app.orchestration.service.assess_cluster", return_value=(mock_assessment, True)):
        _run_assess(db, RUN_DATE, max_assess=10, cfg=None)

    mock_batch.assert_not_called()


def test_run_assess_batch_result_includes_all_step_fields(db):
    """Step details include eligible, skipped_gate, capped plus batch fields."""
    from app.orchestration.service import _run_assess

    pub = datetime(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day, 12, tzinfo=timezone.utc)
    for i in range(3):
        _make_db_cluster(db, published_at=pub, company_suffix=str(i))

    cfg = _make_settings(use_batch_assess=True)
    batch_return = {
        "mode": "batch", "batch_id": "b1", "submitted": 2,
        "succeeded": 2, "failed": 0, "timed_out": False,
        "poll_duration_seconds": 1.0,
    }

    with patch("app.orchestration.service.assess_cluster_batch_run", return_value=batch_return):
        result = _run_assess(db, RUN_DATE, max_assess=2, cfg=cfg)

    # Orchestration fields
    assert result["eligible"] == 3
    assert result["capped"] is True
    # Batch fields merged in
    assert result["batch_id"] == "b1"
    assert result["submitted"] == 2
    assert result["succeeded"] == 2
    assert result["timed_out"] is False
    assert result["poll_duration_seconds"] == 1.0


def test_run_assess_skipped_gate_counted_before_submission(db):
    """Clusters that fail section gates are counted as skipped_gate, not submitted."""
    from app.orchestration.service import _run_assess

    pub = datetime(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day, 12, tzinfo=timezone.utc)
    # Two clusters: one passes gate, one fails
    cluster_pass = _make_db_cluster(db, published_at=pub, company_suffix="A")
    cluster_fail = _make_db_cluster(db, published_at=pub, company_suffix="B")

    cfg = _make_settings(use_batch_assess=True)
    batch_return = {
        "mode": "batch", "batch_id": "b1", "submitted": 1,
        "succeeded": 1, "failed": 0, "timed_out": False,
        "poll_duration_seconds": 0.5,
    }

    # Gate passes for cluster_pass only
    def gate_side_effect(db_arg, cluster):
        return cluster.id == cluster_pass.id

    with patch("app.orchestration.service.cluster_passes_any_section_gate", side_effect=gate_side_effect), \
         patch("app.orchestration.service.assess_cluster_batch_run", return_value=batch_return) as mock_batch:
        result = _run_assess(db, RUN_DATE, max_assess=10, cfg=cfg)

    assert result["eligible"] == 2
    assert result["skipped_gate"] == 1
    # Only the passing cluster was submitted
    submitted_clusters = mock_batch.call_args[0][1]
    assert len(submitted_clusters) == 1
    assert submitted_clusters[0].id == cluster_pass.id


def test_run_assess_cap_applied_before_batch_submission(db):
    """Cap is applied before branching — batch receives only capped subset."""
    from app.orchestration.service import _run_assess

    pub = datetime(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day, 12, tzinfo=timezone.utc)
    for i in range(5):
        _make_db_cluster(db, published_at=pub, company_suffix=str(i))

    cfg = _make_settings(use_batch_assess=True)
    batch_return = {
        "mode": "batch", "batch_id": "b1", "submitted": 2,
        "succeeded": 2, "failed": 0, "timed_out": False,
        "poll_duration_seconds": 1.0,
    }

    with patch("app.orchestration.service.assess_cluster_batch_run", return_value=batch_return) as mock_batch:
        result = _run_assess(db, RUN_DATE, max_assess=2, cfg=cfg)

    assert result["eligible"] == 5
    assert result["capped"] is True
    # Batch received exactly 2 clusters (the cap)
    submitted_clusters = mock_batch.call_args[0][1]
    assert len(submitted_clusters) == 2


def test_run_assess_timeout_propagates_as_step_failure(db):
    """BatchTimeoutError from batch propagates out of _run_assess — no sync fallback."""
    from app.orchestration.service import _run_assess

    pub = datetime(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day, 12, tzinfo=timezone.utc)
    _make_db_cluster(db, published_at=pub)
    cfg = _make_settings(use_batch_assess=True)

    with patch("app.orchestration.service.assess_cluster_batch_run",
               side_effect=BatchTimeoutError("timed out")), \
         patch("app.orchestration.service.assess_cluster") as mock_sync:
        with pytest.raises(BatchTimeoutError):
            _run_assess(db, RUN_DATE, max_assess=10, cfg=cfg)

    # Sync path must NOT be called as a fallback
    mock_sync.assert_not_called()


# ── config loading ────────────────────────────────────────────────────────────

def test_batch_assess_config_defaults():
    s = load_settings(config_path="/nonexistent/path")
    assert s.llm.use_batch_assess is False
    assert s.llm.batch_poll_interval_seconds == 30
    assert s.llm.batch_timeout_minutes == 90


def test_batch_assess_config_loaded_from_yaml(tmp_path):
    f = tmp_path / "settings.yaml"
    f.write_text(
        "llm:\n"
        "  use_batch_assess: true\n"
        "  batch_poll_interval_seconds: 60\n"
        "  batch_timeout_minutes: 120\n"
    )
    s = load_settings(config_path=str(f))
    assert s.llm.use_batch_assess is True
    assert s.llm.batch_poll_interval_seconds == 60
    assert s.llm.batch_timeout_minutes == 120
