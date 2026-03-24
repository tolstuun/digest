"""
Extraction service: orchestrates LLM call and persists StoryFacts.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.extraction.llm import extract_facts_llm
from app.extraction.schemas import StoryInput
from app.models.raw_item import RawItem
from app.models.story import Story
from app.models.story_facts import StoryFacts

logger = logging.getLogger(__name__)


def extract_story_facts(db: Session, story: Story) -> tuple[StoryFacts, bool]:
    """
    Extract facts from *story* using LLM and persist to story_facts.

    Returns (StoryFacts, created) where created=True if a new row was inserted,
    False if an existing row was updated (idempotent upsert).
    """
    # Build LLM input from the story's raw_item payload
    raw_item = db.get(RawItem, story.raw_item_id)
    raw_payload: dict = (raw_item.raw_payload or {}) if raw_item else {}

    story_input = StoryInput(
        story_id=str(story.id),
        title=story.title or raw_payload.get("title"),
        text=raw_payload.get("summary") or raw_payload.get("text"),
        url=story.canonical_url or story.url,
    )

    result = extract_facts_llm(story_input)
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

    logger.info(
        "extract_story_facts story=%s event_type=%s confidence=%.2f created=%s",
        story.id, facts.event_type, facts.extraction_confidence or 0, created,
    )
    return facts, created
