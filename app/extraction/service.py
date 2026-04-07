"""
Extraction service: orchestrates LLM call and persists StoryFacts.

Sync path:  extract_story_facts() — one story, one Anthropic call, one DB row.
Batch path: extract_story_facts_batch_run() — N stories, one batch, N DB rows.

Both paths call _apply_facts() to persist StoryFacts and record LLM usage.
Usage is recorded only for succeeded items; errored batch items produce no rows.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.extraction.batch import BatchItemResult, extract_facts_batch
from app.extraction.llm import extract_facts_llm
from app.extraction.schemas import ExtractionResult, StoryInput
from app.llm_usage.schemas import LlmUsageInfo
from app.llm_usage.service import record_usage
from app.models.raw_item import RawItem
from app.models.story import Story
from app.models.story_facts import StoryFacts

logger = logging.getLogger(__name__)


def _build_story_input(db: Session, story: Story) -> StoryInput:
    """Build StoryInput for LLM from a Story and its raw_item payload."""
    raw_item = db.get(RawItem, story.raw_item_id)
    raw_payload: dict = (raw_item.raw_payload or {}) if raw_item else {}
    return StoryInput(
        story_id=str(story.id),
        title=story.title or raw_payload.get("title"),
        text=raw_payload.get("summary") or raw_payload.get("text"),
        url=story.canonical_url or story.url,
    )


def _apply_facts(
    db: Session,
    story: Story,
    result: ExtractionResult,
    llm_usage: LlmUsageInfo,
    pipeline_run_id: Optional[uuid.UUID] = None,
) -> tuple[StoryFacts, bool]:
    """
    Persist ExtractionResult to StoryFacts and record LLM usage.

    Shared by the sync (extract_story_facts) and batch
    (extract_story_facts_batch_run) paths. Returns (StoryFacts, created).

    Usage is always recorded here with the real token counts from llm_usage.
    Callers must NOT pass llm_usage=None — for items with no real usage
    (errored batch items) skip this call entirely rather than fabricating zeros.
    """
    raw_output = result.model_dump()

    existing = db.query(StoryFacts).filter_by(story_id=story.id).first()
    created = existing is None

    if existing is None:
        facts = StoryFacts(story_id=story.id)
        db.add(facts)
    else:
        facts = existing

    facts.model_name = settings.extraction_model
    facts.raw_model_output = raw_output
    facts.extraction_confidence = result.extraction_confidence
    facts.extracted_at = datetime.now(timezone.utc)
    facts.source_language = result.source_language
    facts.event_type = result.event_type
    facts.company_names = result.company_names
    facts.person_names = result.person_names
    facts.product_names = result.product_names
    facts.geography_names = result.geography_names
    facts.amount_text = result.amount_text
    facts.currency = result.currency
    facts.canonical_summary_en = result.canonical_summary_en
    facts.canonical_summary_ru = result.canonical_summary_ru

    db.commit()
    db.refresh(facts)

    record_usage(db, "extract_facts", llm_usage, pipeline_run_id=pipeline_run_id)

    logger.info(
        "apply_facts story=%s event_type=%s confidence=%.2f created=%s",
        story.id, facts.event_type, facts.extraction_confidence or 0, created,
    )
    return facts, created


def extract_story_facts(
    db: Session,
    story: Story,
    pipeline_run_id: Optional[uuid.UUID] = None,
    output_language: Optional[str] = None,
) -> tuple[StoryFacts, bool]:
    """
    Extract facts from *story* using LLM and persist to story_facts.

    Returns (StoryFacts, created) where created=True if a new row was inserted,
    False if an existing row was updated (idempotent upsert).
    """
    story_input = _build_story_input(db, story)
    lang = output_language or settings.digest.output_language
    result, llm_usage = extract_facts_llm(story_input, output_language=lang)
    return _apply_facts(db, story, result, llm_usage, pipeline_run_id=pipeline_run_id)


def extract_story_facts_batch_run(
    db: Session,
    stories: list[Story],
    cfg: Settings,
    pipeline_run_id: Optional[uuid.UUID] = None,
    output_language: Optional[str] = None,
) -> dict:
    """
    Run batch extraction for a list of stories via Anthropic Message Batches.

    Returns a dict with batch observability fields:
      mode, batch_id, submitted, succeeded, failed, timed_out,
      poll_duration_seconds, new, updated.

    Raises BatchTimeoutError if the batch does not complete within
    cfg.llm.batch_timeout_minutes. There is NO sync fallback — the caller
    must treat BatchTimeoutError as a hard step failure.

    Usage is recorded (with pipeline_run_id) for every succeeded item using
    the real per-item token counts from the batch result. Errored/canceled/
    expired items produce no LlmUsage rows — not zero-cost phantom rows.
    """
    lang = output_language or cfg.digest.output_language
    story_map = {str(s.id): s for s in stories}
    story_inputs = [(str(s.id), _build_story_input(db, s)) for s in stories]

    batch_id, results, poll_duration_seconds = extract_facts_batch(
        story_inputs=story_inputs,
        api_key=cfg.llm.api_key,
        model=cfg.llm.model_extraction,
        output_language=lang,
        poll_interval_seconds=cfg.llm.batch_poll_interval_seconds,
        timeout_minutes=cfg.llm.batch_timeout_minutes,
    )

    succeeded = failed = new = updated = 0
    for item in results:
        if item.error is not None:
            # usage=None for errored items — deliberately not recorded
            failed += 1
            logger.warning(
                "batch_extract item failed story=%s: %s", item.story_id, item.error
            )
            continue
        story = story_map.get(item.story_id)
        if story is None:
            logger.error(
                "batch_extract result story_id=%s not in story_map", item.story_id
            )
            failed += 1
            continue
        # item.usage carries real token counts; _apply_facts records them
        _, created = _apply_facts(
            db, story, item.result, item.usage, pipeline_run_id=pipeline_run_id
        )
        succeeded += 1
        if created:
            new += 1
        else:
            updated += 1

    return {
        "mode": "batch",
        "batch_id": batch_id,
        "submitted": len(stories),
        "succeeded": succeeded,
        "failed": failed,
        "timed_out": False,
        "poll_duration_seconds": poll_duration_seconds,
        "new": new,
        "updated": updated,
    }
