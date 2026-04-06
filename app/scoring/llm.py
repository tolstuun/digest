"""
LLM boundary for editorial scoring.
Single function — all service tests mock at this name.

Returns (ClusterAssessment, LlmUsageInfo).
"""
import logging

import anthropic

from app.config import settings
from app.llm_usage.errors import raise_if_billing_error
from app.llm_usage.schemas import LlmUsageInfo
from app.scoring.schemas import ClusterAssessment, ClusterInput

logger = logging.getLogger(__name__)

_TOOL_NAME = "assess_cluster"

_TOOL_PROPERTIES = {
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
}

_BASE_REQUIRED = [
    "primary_section",
    "llm_score",
    "include_in_digest",
    "editorial_notes",
]

# Language-instruction appended to the prompt based on output_language.
_LANG_PROMPT = {
    "en": (
        "Output language instruction: generate ONLY the English why-it-matters field "
        "(why_it_matters_en). Do NOT generate why_it_matters_ru — "
        "leave it empty or omit it. Do not spend tokens producing Russian text."
    ),
    "ru": (
        "Output language instruction: generate ONLY the Russian why-it-matters field "
        "(why_it_matters_ru). Do NOT generate why_it_matters_en — "
        "leave it empty or omit it. Do not spend tokens producing English text."
    ),
}


def _build_tool_schema(output_language: str) -> dict:
    """Return the tool schema with only the active language's field in required."""
    if output_language == "ru":
        required_why = ["why_it_matters_ru"]
    else:
        required_why = ["why_it_matters_en"]

    return {
        "name": _TOOL_NAME,
        "description": (
            "Provide an editorial assessment of this cybersecurity news event cluster "
            "for inclusion in a daily business digest. "
            "Score objectively: 0.0 = no reader value, 1.0 = top-tier business story."
        ),
        "input_schema": {
            "type": "object",
            "properties": _TOOL_PROPERTIES,
            "required": _BASE_REQUIRED + required_why,
        },
    }


def assess_cluster_llm(
    cluster_input: ClusterInput,
    output_language: str = "en",
) -> tuple[ClusterAssessment, LlmUsageInfo]:
    """
    Call Anthropic with tool-use to produce an editorial assessment.

    output_language controls which why-it-matters field is required in the
    tool schema and adds an explicit instruction to skip the unused language.

    Returns (ClusterAssessment, LlmUsageInfo).
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    companies_str = ", ".join(cluster_input.company_names) if cluster_input.company_names else "unknown"
    amount_str = (
        f"{cluster_input.amount_text} {cluster_input.currency or ''}".strip()
        if cluster_input.amount_text
        else "N/A"
    )

    lang_instruction = _LANG_PROMPT.get(output_language, _LANG_PROMPT["en"])
    prompt = (
        f"{lang_instruction}\n\n"
        f"Event type: {cluster_input.event_type or 'unknown'}\n"
        f"Companies: {companies_str}\n"
        f"Deal size: {amount_str}\n"
        f"Sources covering this event: {cluster_input.story_count}\n"
        f"Title: {cluster_input.representative_title or 'N/A'}\n"
        f"Summary: {cluster_input.canonical_summary_en or cluster_input.canonical_summary_ru or 'N/A'}"
    )

    tool_schema = _build_tool_schema(output_language)

    try:
        response = client.messages.create(
            model=settings.scoring_model,
            max_tokens=512,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        raise_if_billing_error(exc)
        raise

    tool_use_block = next(b for b in response.content if b.type == "tool_use")
    result = ClusterAssessment(**tool_use_block.input)
    usage = LlmUsageInfo(
        model_name=settings.scoring_model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        related_object_id=cluster_input.cluster_id,
    )
    return result, usage
