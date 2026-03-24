"""
LLM boundary for editorial scoring.
Single function — all service tests mock at this name.
"""
import logging

import anthropic

from app.config import settings
from app.scoring.schemas import ClusterAssessment, ClusterInput

logger = logging.getLogger(__name__)

_TOOL_NAME = "assess_cluster"

_TOOL_SCHEMA = {
    "name": _TOOL_NAME,
    "description": (
        "Provide an editorial assessment of this cybersecurity news event cluster "
        "for inclusion in a daily business digest. "
        "Score objectively: 0.0 = no reader value, 1.0 = top-tier business story."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "primary_section": {
                "type": "string",
                "enum": [
                    "companies_business",
                    "incidents",
                    "conferences",
                    "regulation",
                    "other",
                ],
                "description": (
                    "companies_business: funding, M&A, earnings, market moves. "
                    "incidents: breaches, outages, attacks. "
                    "conferences: events, summits. "
                    "regulation: policy, law, compliance. "
                    "other: anything that does not fit above."
                ),
            },
            "llm_score": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Editorial significance score (0.0–1.0).",
            },
            "include_in_digest": {
                "type": "boolean",
                "description": "True if this cluster should appear in today's digest.",
            },
            "why_it_matters_en": {
                "type": "string",
                "description": "1–2 sentences explaining business significance in English.",
            },
            "why_it_matters_ru": {
                "type": "string",
                "description": "1–2 sentences explaining business significance in Russian.",
            },
            "editorial_notes": {
                "type": "string",
                "description": "Short internal note for editors (may be empty string).",
            },
        },
        "required": [
            "primary_section",
            "llm_score",
            "include_in_digest",
            "why_it_matters_en",
            "why_it_matters_ru",
            "editorial_notes",
        ],
    },
}


def assess_cluster_llm(cluster_input: ClusterInput) -> ClusterAssessment:
    """Call Anthropic with tool-use to produce an editorial assessment."""
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    companies_str = ", ".join(cluster_input.company_names) if cluster_input.company_names else "unknown"
    amount_str = (
        f"{cluster_input.amount_text} {cluster_input.currency or ''}".strip()
        if cluster_input.amount_text
        else "N/A"
    )

    prompt = (
        f"Event type: {cluster_input.event_type or 'unknown'}\n"
        f"Companies: {companies_str}\n"
        f"Deal size: {amount_str}\n"
        f"Sources covering this event: {cluster_input.story_count}\n"
        f"Title: {cluster_input.representative_title or 'N/A'}\n"
        f"Summary: {cluster_input.canonical_summary_en or 'N/A'}"
    )

    response = client.messages.create(
        model=settings.extraction_model,
        max_tokens=512,
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[{"role": "user", "content": prompt}],
    )

    tool_use_block = next(b for b in response.content if b.type == "tool_use")
    return ClusterAssessment(**tool_use_block.input)
