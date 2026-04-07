"""
Anthropic Message Batches for extract_facts.

extract_facts_batch() submits all stories in one batch request, polls until
"ended", and returns per-story results.

Language-aware schema and prompt are reused verbatim from extraction/llm.py
(same _build_tool_schema, same _LANG_PROMPT) so the PR #40 cost-saving
behavior (unused summary field not generated) is fully preserved in batch mode.

Failure modes:
  - BatchTimeoutError: raised when the batch does not reach "ended" within
    the configured timeout. The caller must treat this as a hard failure.
    There is NO sync fallback — this is intentional to avoid surprise costs.
  - Per-item errors: captured in BatchItemResult.error; not raised.
    BatchItemResult.usage is None for errored/canceled/expired items.
    record_usage() is deliberately NOT called for failed items — there are
    no phantom zero-cost rows.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import anthropic

from app.extraction.llm import _LANG_PROMPT, _TOOL_NAME, _build_tool_schema
from app.extraction.schemas import ExtractionResult, StoryInput
from app.llm_usage.schemas import LlmUsageInfo

logger = logging.getLogger(__name__)


class BatchTimeoutError(Exception):
    """Raised when an Anthropic batch does not complete within the configured timeout."""


@dataclass
class BatchItemResult:
    story_id: str
    result: Optional[ExtractionResult]  # None for errored/canceled/expired items
    usage: Optional[LlmUsageInfo]       # None for non-succeeded items (never phantom zero-cost)
    error: Optional[str]                # error description; None for succeeded items


def extract_facts_batch(
    story_inputs: list[tuple[str, StoryInput]],
    api_key: str,
    model: str,
    output_language: str,
    poll_interval_seconds: int,
    timeout_minutes: int,
) -> tuple[str, list[BatchItemResult], float]:
    """
    Submit story_inputs as one Anthropic Message Batch and poll until complete.

    Returns (batch_id, results, poll_duration_seconds).

    Raises BatchTimeoutError if the batch does not reach "ended" within
    timeout_minutes. The step must be treated as failed — no sync fallback.

    For succeeded items: result and usage are populated with real token counts.
    For errored/canceled/expired items: result=None, usage=None, error=description.
    """
    client = anthropic.Anthropic(api_key=api_key)
    tool_schema = _build_tool_schema(output_language)
    lang_instruction = _LANG_PROMPT.get(output_language, _LANG_PROMPT["en"])

    requests = []
    for story_id, story_input in story_inputs:
        text_parts = []
        if story_input.title:
            text_parts.append(f"Title: {story_input.title}")
        if story_input.text:
            text_parts.append(f"Text: {story_input.text}")
        if story_input.url:
            text_parts.append(f"URL: {story_input.url}")
        content = "\n\n".join(text_parts) or "(no content)"
        prompt = f"{lang_instruction}\n\n{content}"

        requests.append({
            "custom_id": story_id,
            "params": {
                "model": model,
                "max_tokens": 1024,
                "tools": [tool_schema],
                "tool_choice": {"type": "tool", "name": _TOOL_NAME},
                "messages": [{"role": "user", "content": prompt}],
            },
        })

    batch = client.messages.batches.create(requests=requests)
    batch_id = batch.id
    logger.info(
        "extract_facts_batch submitted batch_id=%s requests=%d",
        batch_id, len(requests),
    )

    poll_start = time.monotonic()
    deadline = poll_start + timeout_minutes * 60

    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            poll_duration = round(time.monotonic() - poll_start, 1)
            raise BatchTimeoutError(
                f"Batch {batch_id} did not complete within {timeout_minutes} minutes "
                f"(poll_duration={poll_duration}s, status={batch.processing_status})"
            )
        wait = min(poll_interval_seconds, remaining)
        logger.debug(
            "extract_facts_batch batch_id=%s status=%s remaining=%.0fs sleeping=%.0fs",
            batch_id, batch.processing_status, remaining, wait,
        )
        time.sleep(wait)

    poll_duration_seconds = round(time.monotonic() - poll_start, 1)
    logger.info(
        "extract_facts_batch batch_id=%s ended poll_duration=%.1fs",
        batch_id, poll_duration_seconds,
    )

    results: list[BatchItemResult] = []
    for item in client.messages.batches.results(batch_id):
        story_id = item.custom_id
        if item.result.type == "succeeded":
            msg = item.result.message
            tool_block = next(
                (b for b in msg.content if b.type == "tool_use"), None
            )
            if tool_block is None:
                results.append(BatchItemResult(
                    story_id=story_id,
                    result=None,
                    usage=None,
                    error="succeeded but no tool_use block in response",
                ))
                continue
            try:
                extraction = ExtractionResult(**tool_block.input)
            except Exception as exc:  # noqa: BLE001
                results.append(BatchItemResult(
                    story_id=story_id,
                    result=None,
                    usage=None,
                    error=f"ExtractionResult parse error: {exc}",
                ))
                continue
            usage = LlmUsageInfo(
                model_name=model,
                input_tokens=msg.usage.input_tokens,
                output_tokens=msg.usage.output_tokens,
                related_object_id=story_id,
            )
            results.append(BatchItemResult(
                story_id=story_id,
                result=extraction,
                usage=usage,
                error=None,
            ))
        else:
            # errored, canceled, or expired — usage unavailable; no phantom row
            error_type = item.result.type
            error_detail = str(getattr(item.result, "error", error_type))
            results.append(BatchItemResult(
                story_id=story_id,
                result=None,
                usage=None,
                error=f"{error_type}: {error_detail}",
            ))

    return batch_id, results, poll_duration_seconds
