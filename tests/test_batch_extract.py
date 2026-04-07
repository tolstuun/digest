"""
Tests for Anthropic Message Batches extract_facts mode.

All Anthropic API calls are mocked — no real network requests.
Tests cover:
  - batch request structure (custom_id, language-aware schema/prompt)
  - result mapping via custom_id → story
  - facts persisted from batch results
  - LLM usage recorded with real token counts and pipeline_run_id
  - errored items produce no LlmUsage rows (no phantom zero-cost rows)
  - BatchTimeoutError raised on timeout, no sync fallback
  - orchestration branching (_run_extract_facts uses batch vs sync)
  - config loading for new fields
"""
import hashlib
import time
import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from app.config import (
    AppConfig, DatabaseConfig, DigestConfig, LLMConfig,
    SchedulerConfig, Settings, TelegramConfig, load_settings,
)
from app.extraction.batch import BatchItemResult, BatchTimeoutError, extract_facts_batch
from app.extraction.schemas import ExtractionResult
from app.llm_usage.schemas import LlmUsageInfo
from app.models.llm_usage import LlmUsage
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts

RUN_DATE = date(2026, 3, 25)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_settings(
    use_batch_extract: bool = False,
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
            model_extraction="claude-haiku-4-5-20251001",
            use_batch_extract=use_batch_extract,
            batch_poll_interval_seconds=batch_poll_interval_seconds,
            batch_timeout_minutes=batch_timeout_minutes,
        ),
        telegram=TelegramConfig(),
        scheduler=SchedulerConfig(),
        digest=DigestConfig(output_language=output_language),
    )


def _make_story_input(story_id: str = None) -> tuple[str, "StoryInput"]:
    from app.extraction.schemas import StoryInput
    sid = story_id or str(uuid.uuid4())
    return sid, StoryInput(
        story_id=sid,
        title="Acme Corp raises $50M",
        text="Acme Corp announced a $50M Series B funding round.",
        url="https://example.com/acme",
    )


def _valid_extraction_input() -> dict:
    return {
        "source_language": "en",
        "event_type": "funding",
        "company_names": ["Acme Corp"],
        "person_names": [],
        "product_names": [],
        "geography_names": ["USA"],
        "amount_text": "$50M",
        "currency": "USD",
        "canonical_summary_en": "Acme raised $50M.",
        "canonical_summary_ru": "",
        "extraction_confidence": 0.92,
    }


def _make_mock_batch(batch_id: str = "batch_test001", status: str = "ended") -> MagicMock:
    batch = MagicMock()
    batch.id = batch_id
    batch.processing_status = status
    return batch


def _make_succeeded_result_item(story_id: str, tool_input: dict, input_tokens: int = 100, output_tokens: int = 50) -> MagicMock:
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
    item.custom_id = story_id
    item.result = result
    return item


def _make_errored_result_item(story_id: str, error_type: str = "errored") -> MagicMock:
    result = MagicMock()
    result.type = error_type
    result.error = f"{error_type} error detail"

    item = MagicMock()
    item.custom_id = story_id
    item.result = result
    return item


def _make_db_story(db, *, published_at: datetime | None = None) -> Story:
    src = Source(
        name=f"Feed-{uuid.uuid4().hex[:8]}",
        type="rss",
        url=f"https://example.com/feed/{uuid.uuid4().hex[:8]}",
        enabled=True,
    )
    db.add(src)
    db.flush()

    content = f"content-{uuid.uuid4()}"
    ri = RawItem(
        source_id=src.id,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        title="Acme raises $50M",
        url=f"https://example.com/{uuid.uuid4().hex[:8]}",
        raw_payload={"title": "Acme raises $50M", "summary": content},
    )
    db.add(ri)
    db.flush()

    story = Story(
        raw_item_id=ri.id,
        source_id=src.id,
        title="Acme raises $50M",
        url=ri.url,
        canonical_url=ri.url,
        published_at=published_at,
    )
    db.add(story)
    db.commit()
    db.refresh(story)
    return story


# ── extract_facts_batch: request structure ────────────────────────────────────

def test_batch_requests_use_story_id_as_custom_id():
    """Each request's custom_id must equal the story_id string."""
    story_id = str(uuid.uuid4())
    sid, story_input = _make_story_input(story_id)
    mock_batch = _make_mock_batch()

    with patch("app.extraction.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = []

        extract_facts_batch(
            story_inputs=[(sid, story_input)],
            api_key="key",
            model="claude-haiku-4-5-20251001",
            output_language="en",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    create_call = client.messages.batches.create.call_args
    requests = create_call.kwargs["requests"]
    assert len(requests) == 1
    assert requests[0]["custom_id"] == story_id


def test_batch_requests_multiple_stories():
    """One request per story, all custom_ids present."""
    inputs = [_make_story_input() for _ in range(3)]
    expected_ids = {sid for sid, _ in inputs}
    mock_batch = _make_mock_batch()

    with patch("app.extraction.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = []

        extract_facts_batch(
            story_inputs=inputs,
            api_key="key",
            model="claude-haiku-4-5-20251001",
            output_language="en",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    create_call = client.messages.batches.create.call_args
    actual_ids = {r["custom_id"] for r in create_call.kwargs["requests"]}
    assert actual_ids == expected_ids


def test_batch_schema_en_requires_en_summary_not_ru():
    """Language-aware schema: EN mode requires canonical_summary_en, not canonical_summary_ru."""
    sid, story_input = _make_story_input()
    mock_batch = _make_mock_batch()

    with patch("app.extraction.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = []

        extract_facts_batch(
            story_inputs=[(sid, story_input)],
            api_key="key",
            model="model",
            output_language="en",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    req = client.messages.batches.create.call_args.kwargs["requests"][0]
    required = req["params"]["tools"][0]["input_schema"]["required"]
    assert "canonical_summary_en" in required
    assert "canonical_summary_ru" not in required


def test_batch_schema_ru_requires_ru_summary_not_en():
    """Language-aware schema: RU mode requires canonical_summary_ru, not canonical_summary_en."""
    sid, story_input = _make_story_input()
    mock_batch = _make_mock_batch()

    with patch("app.extraction.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = []

        extract_facts_batch(
            story_inputs=[(sid, story_input)],
            api_key="key",
            model="model",
            output_language="ru",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    req = client.messages.batches.create.call_args.kwargs["requests"][0]
    required = req["params"]["tools"][0]["input_schema"]["required"]
    assert "canonical_summary_ru" in required
    assert "canonical_summary_en" not in required


def test_batch_prompt_en_mode_instructs_skip_ru():
    """EN mode prompt explicitly tells LLM not to generate canonical_summary_ru."""
    sid, story_input = _make_story_input()
    mock_batch = _make_mock_batch()

    with patch("app.extraction.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = []

        extract_facts_batch(
            story_inputs=[(sid, story_input)],
            api_key="key",
            model="model",
            output_language="en",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    req = client.messages.batches.create.call_args.kwargs["requests"][0]
    prompt = req["params"]["messages"][0]["content"]
    assert "Do NOT generate canonical_summary_ru" in prompt


def test_batch_prompt_ru_mode_instructs_skip_en():
    """RU mode prompt explicitly tells LLM not to generate canonical_summary_en."""
    sid, story_input = _make_story_input()
    mock_batch = _make_mock_batch()

    with patch("app.extraction.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = []

        extract_facts_batch(
            story_inputs=[(sid, story_input)],
            api_key="key",
            model="model",
            output_language="ru",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    req = client.messages.batches.create.call_args.kwargs["requests"][0]
    prompt = req["params"]["messages"][0]["content"]
    assert "Do NOT generate canonical_summary_en" in prompt


# ── extract_facts_batch: result parsing ───────────────────────────────────────

def test_batch_succeeded_result_parsed_correctly():
    """A succeeded result is parsed into ExtractionResult with correct usage."""
    story_id = str(uuid.uuid4())
    sid, story_input = _make_story_input(story_id)
    tool_input = _valid_extraction_input()
    mock_batch = _make_mock_batch()
    result_item = _make_succeeded_result_item(story_id, tool_input, input_tokens=120, output_tokens=60)

    with patch("app.extraction.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = [result_item]

        _, results, _ = extract_facts_batch(
            story_inputs=[(sid, story_input)],
            api_key="key",
            model="claude-haiku-4-5-20251001",
            output_language="en",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    assert len(results) == 1
    item = results[0]
    assert item.story_id == story_id
    assert item.error is None
    assert item.result is not None
    assert item.result.event_type == "funding"
    assert item.result.company_names == ["Acme Corp"]
    assert item.usage is not None
    assert item.usage.input_tokens == 120
    assert item.usage.output_tokens == 60
    assert item.usage.related_object_id == story_id


def test_batch_errored_result_has_usage_none():
    """Errored batch items must have usage=None — never phantom zero-cost rows."""
    story_id = str(uuid.uuid4())
    sid, story_input = _make_story_input(story_id)
    mock_batch = _make_mock_batch()
    result_item = _make_errored_result_item(story_id, error_type="errored")

    with patch("app.extraction.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = [result_item]

        _, results, _ = extract_facts_batch(
            story_inputs=[(sid, story_input)],
            api_key="key",
            model="model",
            output_language="en",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    assert len(results) == 1
    item = results[0]
    assert item.usage is None
    assert item.result is None
    assert item.error is not None
    assert "errored" in item.error


def test_batch_returns_batch_id_and_poll_duration():
    """Return tuple includes batch_id string and non-negative poll_duration_seconds."""
    sid, story_input = _make_story_input()
    mock_batch = _make_mock_batch(batch_id="msgbatch_abc123")

    with patch("app.extraction.batch.anthropic.Anthropic") as MockClient:
        client = MockClient.return_value
        client.messages.batches.create.return_value = mock_batch
        client.messages.batches.retrieve.return_value = mock_batch
        client.messages.batches.results.return_value = []

        batch_id, results, poll_duration = extract_facts_batch(
            story_inputs=[(sid, story_input)],
            api_key="key",
            model="model",
            output_language="en",
            poll_interval_seconds=1,
            timeout_minutes=1,
        )

    assert batch_id == "msgbatch_abc123"
    assert isinstance(poll_duration, float)
    assert poll_duration >= 0


# ── extract_facts_batch: timeout behavior ─────────────────────────────────────

def _make_monotonic_that_times_out(poll_count_before_timeout: int = 1):
    """
    Return a monotonic() mock that simulates timeout after N polls.
    First two calls set poll_start=0 and deadline=timeout_seconds.
    After poll_count_before_timeout retrieves, remaining drops below zero.
    """
    calls = {"n": 0}

    def monotonic():
        calls["n"] += 1
        if calls["n"] <= 2:
            return 0.0
        # Past the deadline on every subsequent call
        return 999999.0

    return monotonic


def test_batch_timeout_raises_batch_timeout_error():
    """When batch never reaches 'ended', BatchTimeoutError is raised."""
    sid, story_input = _make_story_input()
    in_progress_batch = _make_mock_batch(status="in_progress")

    with (
        patch("app.extraction.batch.anthropic.Anthropic") as MockClient,
        patch("app.extraction.batch.time.monotonic", side_effect=_make_monotonic_that_times_out()),
        patch("app.extraction.batch.time.sleep"),
    ):
        client = MockClient.return_value
        client.messages.batches.create.return_value = in_progress_batch
        client.messages.batches.retrieve.return_value = in_progress_batch

        with pytest.raises(BatchTimeoutError) as exc_info:
            extract_facts_batch(
                story_inputs=[(sid, story_input)],
                api_key="key",
                model="model",
                output_language="en",
                poll_interval_seconds=30,
                timeout_minutes=90,
            )

    assert "did not complete within" in str(exc_info.value)
    assert "90 minutes" in str(exc_info.value)


def test_batch_timeout_batch_results_not_called():
    """On timeout, results() is never called — no partial cost surprise."""
    sid, story_input = _make_story_input()
    in_progress_batch = _make_mock_batch(status="in_progress")

    with (
        patch("app.extraction.batch.anthropic.Anthropic") as MockClient,
        patch("app.extraction.batch.time.monotonic", side_effect=_make_monotonic_that_times_out()),
        patch("app.extraction.batch.time.sleep"),
    ):
        client = MockClient.return_value
        client.messages.batches.create.return_value = in_progress_batch
        client.messages.batches.retrieve.return_value = in_progress_batch

        with pytest.raises(BatchTimeoutError):
            extract_facts_batch(
                story_inputs=[(sid, story_input)],
                api_key="key",
                model="model",
                output_language="en",
                poll_interval_seconds=30,
                timeout_minutes=90,
            )

    client.messages.batches.results.assert_not_called()


def test_batch_polls_until_ended():
    """Status is polled repeatedly until 'ended'; sleep is called between polls."""
    sid, story_input = _make_story_input()
    in_progress = _make_mock_batch(status="in_progress")
    ended = _make_mock_batch(status="ended")

    # First retrieve returns in_progress, second returns ended
    retrieve_sequence = [in_progress, ended]

    with (
        patch("app.extraction.batch.anthropic.Anthropic") as MockClient,
        patch("app.extraction.batch.time.sleep") as mock_sleep,
    ):
        client = MockClient.return_value
        client.messages.batches.create.return_value = in_progress
        client.messages.batches.retrieve.side_effect = retrieve_sequence
        client.messages.batches.results.return_value = []

        extract_facts_batch(
            story_inputs=[(sid, story_input)],
            api_key="key",
            model="model",
            output_language="en",
            poll_interval_seconds=30,
            timeout_minutes=90,
        )

    assert client.messages.batches.retrieve.call_count == 2
    mock_sleep.assert_called_once()


# ── extract_story_facts_batch_run: DB integration ────────────────────────────

def test_batch_run_persists_facts_for_succeeded_items(db):
    """Facts are persisted to StoryFacts for each succeeded batch item."""
    story = _make_db_story(db, published_at=datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc))
    story_id = str(story.id)
    cfg = _make_settings(use_batch_extract=True)

    tool_input = _valid_extraction_input()
    mock_batch_result = (
        story_id,
        [BatchItemResult(
            story_id=story_id,
            result=ExtractionResult(**tool_input),
            usage=LlmUsageInfo(model_name="claude-haiku-4-5-20251001", input_tokens=100, output_tokens=50, related_object_id=story_id),
            error=None,
        )],
        1.5,
    )

    from app.extraction.service import extract_story_facts_batch_run
    with patch("app.extraction.service.extract_facts_batch", return_value=mock_batch_result):
        result = extract_story_facts_batch_run(db, [story], cfg)

    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert result["new"] == 1
    facts = db.query(StoryFacts).filter_by(story_id=story.id).first()
    assert facts is not None
    assert facts.event_type == "funding"
    assert facts.company_names == ["Acme Corp"]


def test_batch_run_records_usage_with_pipeline_run_id(db):
    """Each succeeded item produces one LlmUsage row with real token counts and pipeline_run_id."""
    from app.models.pipeline_run import PipelineRun

    story = _make_db_story(db)
    story_id = str(story.id)
    cfg = _make_settings(use_batch_extract=True)

    # pipeline_run_id FK requires a real PipelineRun row
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
        "batch_abc",
        [BatchItemResult(
            story_id=story_id,
            result=ExtractionResult(**_valid_extraction_input()),
            usage=LlmUsageInfo(model_name="claude-haiku-4-5-20251001", input_tokens=123, output_tokens=67, related_object_id=story_id),
            error=None,
        )],
        2.0,
    )

    from app.extraction.service import extract_story_facts_batch_run
    with patch("app.extraction.service.extract_facts_batch", return_value=mock_batch_result):
        extract_story_facts_batch_run(db, [story], cfg, pipeline_run_id=run_id)

    usage_row = db.query(LlmUsage).filter_by(pipeline_run_id=run_id).first()
    assert usage_row is not None
    assert usage_row.input_tokens == 123
    assert usage_row.output_tokens == 67
    assert usage_row.stage_name == "extract_facts"


def test_batch_run_no_usage_row_for_failed_items(db):
    """Errored batch items produce no LlmUsage rows — no phantom zero-cost records."""
    story = _make_db_story(db)
    story_id = str(story.id)
    cfg = _make_settings(use_batch_extract=True)
    run_id = uuid.uuid4()

    mock_batch_result = (
        "batch_abc",
        [BatchItemResult(
            story_id=story_id,
            result=None,
            usage=None,  # explicit None for errored item
            error="errored: some API error",
        )],
        1.0,
    )

    from app.extraction.service import extract_story_facts_batch_run
    with patch("app.extraction.service.extract_facts_batch", return_value=mock_batch_result):
        result = extract_story_facts_batch_run(db, [story], cfg, pipeline_run_id=run_id)

    assert result["failed"] == 1
    assert result["succeeded"] == 0
    usage_count = db.query(LlmUsage).filter_by(pipeline_run_id=run_id).count()
    assert usage_count == 0


def test_batch_run_result_dict_has_required_observability_fields(db):
    """Return dict contains all observability fields: batch_id, submitted, succeeded, failed, etc."""
    story = _make_db_story(db)
    story_id = str(story.id)
    cfg = _make_settings(use_batch_extract=True)

    mock_batch_result = (
        "msgbatch_obs001",
        [BatchItemResult(
            story_id=story_id,
            result=ExtractionResult(**_valid_extraction_input()),
            usage=LlmUsageInfo(model_name="test", input_tokens=10, output_tokens=5),
            error=None,
        )],
        3.7,
    )

    from app.extraction.service import extract_story_facts_batch_run
    with patch("app.extraction.service.extract_facts_batch", return_value=mock_batch_result):
        result = extract_story_facts_batch_run(db, [story], cfg)

    assert result["mode"] == "batch"
    assert result["batch_id"] == "msgbatch_obs001"
    assert result["submitted"] == 1
    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert result["timed_out"] is False
    assert result["poll_duration_seconds"] == 3.7
    assert result["new"] == 1
    assert result["updated"] == 0


def test_batch_run_updated_not_new_on_re_extraction(db):
    """Re-extracting a story that already has StoryFacts counts as 'updated', not 'new'."""
    story = _make_db_story(db)
    story_id = str(story.id)
    # Pre-populate StoryFacts
    existing_facts = StoryFacts(
        story_id=story.id,
        event_type="funding",
        model_name="old-model",
        extracted_at=datetime.now(timezone.utc),
    )
    db.add(existing_facts)
    db.commit()

    cfg = _make_settings(use_batch_extract=True)
    mock_batch_result = (
        "batch_re",
        [BatchItemResult(
            story_id=story_id,
            result=ExtractionResult(**_valid_extraction_input()),
            usage=LlmUsageInfo(model_name="test", input_tokens=10, output_tokens=5),
            error=None,
        )],
        1.0,
    )

    from app.extraction.service import extract_story_facts_batch_run
    with patch("app.extraction.service.extract_facts_batch", return_value=mock_batch_result):
        result = extract_story_facts_batch_run(db, [story], cfg)

    assert result["new"] == 0
    assert result["updated"] == 1


# ── _run_extract_facts: orchestration branching ───────────────────────────────

def test_run_extract_facts_uses_batch_when_flag_set(db):
    """_run_extract_facts calls extract_story_facts_batch_run when use_batch_extract=True."""
    from app.orchestration.service import _run_extract_facts

    story = _make_db_story(db, published_at=datetime(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day, 12, tzinfo=timezone.utc))
    cfg = _make_settings(use_batch_extract=True)

    batch_return = {
        "mode": "batch", "batch_id": "b1", "submitted": 1,
        "succeeded": 1, "failed": 0, "timed_out": False,
        "poll_duration_seconds": 1.0, "new": 1, "updated": 0,
    }

    with patch("app.orchestration.service.extract_story_facts_batch_run", return_value=batch_return) as mock_batch, \
         patch("app.orchestration.service.extract_story_facts") as mock_sync:
        result = _run_extract_facts(db, RUN_DATE, max_facts=10, cfg=cfg)

    mock_batch.assert_called_once()
    mock_sync.assert_not_called()
    assert result["mode"] == "batch"
    assert result["eligible"] == 1
    assert result["capped"] is False


def test_run_extract_facts_uses_sync_when_flag_false(db):
    """_run_extract_facts uses sync per-story loop when use_batch_extract=False."""
    from app.orchestration.service import _run_extract_facts
    from app.extraction.schemas import ExtractionResult

    story = _make_db_story(db, published_at=datetime(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day, 12, tzinfo=timezone.utc))
    cfg = _make_settings(use_batch_extract=False)

    mock_facts = StoryFacts(story_id=story.id, event_type="funding", model_name="test")

    with patch("app.orchestration.service.extract_story_facts_batch_run") as mock_batch, \
         patch("app.orchestration.service.extract_story_facts", return_value=(mock_facts, True)) as mock_sync:
        result = _run_extract_facts(db, RUN_DATE, max_facts=10, cfg=cfg)

    mock_batch.assert_not_called()
    mock_sync.assert_called_once()
    assert "processed" in result
    assert "mode" not in result


def test_run_extract_facts_uses_sync_when_cfg_is_none(db):
    """_run_extract_facts falls back to sync when cfg=None (default)."""
    from app.orchestration.service import _run_extract_facts

    story = _make_db_story(db, published_at=datetime(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day, 12, tzinfo=timezone.utc))
    mock_facts = StoryFacts(story_id=story.id, event_type="funding", model_name="test")

    with patch("app.orchestration.service.extract_story_facts_batch_run") as mock_batch, \
         patch("app.orchestration.service.extract_story_facts", return_value=(mock_facts, True)):
        _run_extract_facts(db, RUN_DATE, max_facts=10, cfg=None)

    mock_batch.assert_not_called()


def test_run_extract_facts_batch_result_includes_eligible_and_capped(db):
    """Batch result is merged with eligible and capped from the query."""
    from app.orchestration.service import _run_extract_facts

    pub = datetime(RUN_DATE.year, RUN_DATE.month, RUN_DATE.day, 12, tzinfo=timezone.utc)
    for _ in range(3):
        _make_db_story(db, published_at=pub)

    cfg = _make_settings(use_batch_extract=True)
    batch_return = {
        "mode": "batch", "batch_id": "b1", "submitted": 2,
        "succeeded": 2, "failed": 0, "timed_out": False,
        "poll_duration_seconds": 1.0, "new": 2, "updated": 0,
    }

    with patch("app.orchestration.service.extract_story_facts_batch_run", return_value=batch_return):
        result = _run_extract_facts(db, RUN_DATE, max_facts=2, cfg=cfg)

    assert result["eligible"] == 3
    assert result["capped"] is True
    assert result["submitted"] == 2
    assert result["batch_id"] == "b1"


# ── config loading ────────────────────────────────────────────────────────────

def test_batch_config_defaults():
    s = load_settings(config_path="/nonexistent/path")
    assert s.llm.use_batch_extract is False
    assert s.llm.batch_poll_interval_seconds == 30
    assert s.llm.batch_timeout_minutes == 90


def test_batch_config_loaded_from_yaml(tmp_path):
    f = tmp_path / "settings.yaml"
    f.write_text(
        "llm:\n"
        "  use_batch_extract: true\n"
        "  batch_poll_interval_seconds: 60\n"
        "  batch_timeout_minutes: 120\n"
    )
    s = load_settings(config_path=str(f))
    assert s.llm.use_batch_extract is True
    assert s.llm.batch_poll_interval_seconds == 60
    assert s.llm.batch_timeout_minutes == 120
