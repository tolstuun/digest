"""
LLM boundary: single function that calls Anthropic and returns ExtractionResult.
All tests mock this function — never call it directly from service tests.
"""
import json
import logging

import anthropic

from app.config import settings
from app.extraction.schemas import ExtractionResult, StoryInput

logger = logging.getLogger(__name__)

_TOOL_NAME = "extract_facts"

_TOOL_SCHEMA = {
    "name": _TOOL_NAME,
    "description": (
        "Extract structured facts from a cybersecurity news article. "
        "Return all fields even if values are empty lists or null."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "source_language": {"type": "string", "description": "ISO 639-1 language code of the source text"},
            "event_type": {
                "type": "string",
                "enum": [
                    "funding", "mna", "earnings", "executive_change", "partnership",
                    "product_launch", "breach", "conference", "regulation", "other", "unknown",
                ],
            },
            "company_names": {"type": "array", "items": {"type": "string"}},
            "person_names": {"type": "array", "items": {"type": "string"}},
            "product_names": {"type": "array", "items": {"type": "string"}},
            "geography_names": {"type": "array", "items": {"type": "string"}},
            "amount_text": {"type": ["string", "null"]},
            "currency": {"type": ["string", "null"]},
            "canonical_summary_en": {"type": "string", "description": "1-2 sentence factual summary in English"},
            "canonical_summary_ru": {"type": "string", "description": "1-2 sentence factual summary in Russian"},
            "extraction_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": [
            "source_language", "event_type", "company_names", "person_names",
            "product_names", "geography_names", "canonical_summary_en",
            "canonical_summary_ru", "extraction_confidence",
        ],
    },
}


def extract_facts_llm(story_input: StoryInput) -> ExtractionResult:
    """Call Anthropic with tool-use to extract structured facts from a story."""
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    text_parts = []
    if story_input.title:
        text_parts.append(f"Title: {story_input.title}")
    if story_input.text:
        text_parts.append(f"Text: {story_input.text}")
    if story_input.url:
        text_parts.append(f"URL: {story_input.url}")
    prompt = "\n\n".join(text_parts) or "(no content)"

    response = client.messages.create(
        model=settings.extraction_model,
        max_tokens=1024,
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[{"role": "user", "content": prompt}],
    )

    tool_use_block = next(b for b in response.content if b.type == "tool_use")
    return ExtractionResult(**tool_use_block.input)
