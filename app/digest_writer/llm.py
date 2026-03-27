"""
LLM boundary for the final digest-writing stage.

Single function — all service tests mock at this name:
  patch("app.digest_writer.service.write_digest_entry_llm", ...)

Returns (DigestEntryOutput, LlmUsageInfo).
"""
import logging

import anthropic

from app.digest_writer.schemas import DigestEntryInput, DigestEntryOutput
from app.llm_usage.schemas import LlmUsageInfo

logger = logging.getLogger(__name__)

_TOOL_NAME = "write_digest_entry"

_TOOL_SCHEMA = {
    "name": _TOOL_NAME,
    "description": (
        "Write polished digest copy for one news entry. "
        "Output must be in the requested language. "
        "Write final_summary as 2-3 clear, factual sentences: state what happened, "
        "name the company, include deal size or key detail if available, and add brief context. "
        "Write final_why_it_matters as 2-3 sentences: explain the business or security significance "
        "concretely — what this means for the market, buyers, or competitors. "
        "Keep both fields tight and businesslike; avoid marketing language."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "final_summary": {
                "type": "string",
                "description": (
                    "2-3 sentence factual summary in the requested language. "
                    "State what happened, name the company, include key figures, "
                    "and add one sentence of context."
                ),
            },
            "final_why_it_matters": {
                "type": "string",
                "description": (
                    "2-3 sentence business/security significance in the requested language. "
                    "Explain concretely what this means for the market, buyers, or competitors. "
                    "Avoid vague phrases; be specific."
                ),
            },
        },
        "required": ["final_summary", "final_why_it_matters"],
    },
}

_LANG_INSTRUCTION = {
    "en": "Write entirely in English.",
    "ru": "Write entirely in Russian (на русском языке).",
}


def write_digest_entry_llm(
    entry_input: DigestEntryInput, model_name: str, api_key: str
) -> tuple[DigestEntryOutput, LlmUsageInfo]:
    """
    Call Anthropic with tool-use to produce final digest copy.
    Returns (DigestEntryOutput, LlmUsageInfo).
    """
    client = anthropic.Anthropic(api_key=api_key)

    lang_instruction = _LANG_INSTRUCTION.get(entry_input.output_language, _LANG_INSTRUCTION["en"])
    companies_str = ", ".join(entry_input.company_names) if entry_input.company_names else "N/A"
    amount_str = (
        f"{entry_input.amount_text} {entry_input.currency or ''}".strip()
        if entry_input.amount_text
        else "N/A"
    )

    prompt = (
        f"{lang_instruction}\n\n"
        f"Event type: {entry_input.event_type}\n"
        f"Companies: {companies_str}\n"
        f"Deal size: {amount_str}\n"
        f"Title: {entry_input.title or 'N/A'}\n"
        f"Existing summary (EN): {entry_input.canonical_summary_en or 'N/A'}\n"
        f"Why it matters (EN): {entry_input.why_it_matters_en or 'N/A'}\n\n"
        "Now write the final digest copy using the tool."
    )

    response = client.messages.create(
        model=model_name,
        max_tokens=768,
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[{"role": "user", "content": prompt}],
    )

    tool_use_block = next(b for b in response.content if b.type == "tool_use")
    result = DigestEntryOutput(**tool_use_block.input)

    usage = LlmUsageInfo(
        model_name=model_name,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        related_object_id=entry_input.entry_id,
    )
    return result, usage
