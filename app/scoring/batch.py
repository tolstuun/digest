"""
Anthropic Message Batches for assess (editorial scoring).

assess_cluster_batch() submits all clusters in one batch request, polls until
"ended", and returns per-cluster results.

Language-aware schema and prompt are reused verbatim from scoring/llm.py
(_build_tool_schema, _LANG_PROMPT, prompt construction) so PR #40 cost-saving
behavior (unused why-it-matters field not generated) is fully preserved.

Inputs MUST already be gate-filtered, date-scoped, and cap-limited by the
caller. This function submits exactly the set it receives — no extra filtering.

Failure modes:
  - BatchTimeoutError: raised when the batch does not reach "ended" within
    the configured timeout. The caller must treat this as a hard failure.
    There is NO sync fallback — intentional to avoid surprise costs.
  - Per-item errors: captured in BatchItemResult.error; not raised.
    BatchItemResult.usage is None for errored/canceled/expired items.
    record_usage() must NOT be called for failed items — no phantom zero-cost rows.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import anthropic

from app.llm_usage.errors import BatchTimeoutError  # shared with extraction.batch
from app.llm_usage.schemas import LlmUsageInfo
from app.scoring.llm import _LANG_PROMPT, _TOOL_NAME, _build_tool_schema
from app.scoring.schemas import ClusterAssessment, ClusterInput

logger = logging.getLogger(__name__)


@dataclass
class BatchItemResult:
    cluster_id: str
    result: Optional[ClusterAssessment]  # None for errored/canceled/expired items
    usage: Optional[LlmUsageInfo]        # None when usage data is unavailable (never phantom zero-cost)
    #   result is None  → item failed (see error)
    #   result is not None, usage is not None → assessment + real token counts
    #   result is not None, usage is None     → assessment OK but per-item usage
    #                                           was absent from the response shape;
    #                                           assessment will be persisted, usage
    #                                           row will NOT be created
    error: Optional[str]                 # set for errored/canceled/expired items; None on success


def assess_cluster_batch(
    cluster_inputs: list[tuple[str, ClusterInput]],
    api_key: str,
    model: str,
    output_language: str,
    poll_interval_seconds: int,
    timeout_minutes: int,
) -> tuple[str, list[BatchItemResult], float]:
    """
    Submit cluster_inputs as one Anthropic Message Batch and poll until complete.

    cluster_inputs must be the final scoped shortlist (already gate-filtered,
    date-scoped, and cap-limited). This function submits exactly what it receives.

    Returns (batch_id, results, poll_duration_seconds).

    Raises BatchTimeoutError if the batch does not reach "ended" within
    timeout_minutes. There is NO sync fallback.

    For succeeded items: result and usage populated with real token counts.
    For errored/canceled/expired items: result=None, usage=None, error=description.
    """
    client = anthropic.Anthropic(api_key=api_key)
    tool_schema = _build_tool_schema(output_language)
    lang_instruction = _LANG_PROMPT.get(output_language, _LANG_PROMPT["en"])

    requests = []
    for cluster_id, cluster_input in cluster_inputs:
        companies_str = (
            ", ".join(cluster_input.company_names)
            if cluster_input.company_names
            else "unknown"
        )
        amount_str = (
            f"{cluster_input.amount_text} {cluster_input.currency or ''}".strip()
            if cluster_input.amount_text
            else "N/A"
        )
        # Prompt matches assess_cluster_llm exactly so results are equivalent
        prompt = (
            f"{lang_instruction}\n\n"
            f"Event type: {cluster_input.event_type or 'unknown'}\n"
            f"Companies: {companies_str}\n"
            f"Deal size: {amount_str}\n"
            f"Sources covering this event: {cluster_input.story_count}\n"
            f"Title: {cluster_input.representative_title or 'N/A'}\n"
            f"Summary: {cluster_input.canonical_summary_en or cluster_input.canonical_summary_ru or 'N/A'}"
        )
        requests.append({
            "custom_id": cluster_id,
            "params": {
                "model": model,
                "max_tokens": 512,
                "tools": [tool_schema],
                "tool_choice": {"type": "tool", "name": _TOOL_NAME},
                "messages": [{"role": "user", "content": prompt}],
            },
        })

    batch = client.messages.batches.create(requests=requests)
    batch_id = batch.id
    logger.info(
        "assess_cluster_batch submitted batch_id=%s requests=%d",
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
            "assess_cluster_batch batch_id=%s status=%s remaining=%.0fs sleeping=%.0fs",
            batch_id, batch.processing_status, remaining, wait,
        )
        time.sleep(wait)

    poll_duration_seconds = round(time.monotonic() - poll_start, 1)
    logger.info(
        "assess_cluster_batch batch_id=%s ended poll_duration=%.1fs",
        batch_id, poll_duration_seconds,
    )

    results: list[BatchItemResult] = []
    for item in client.messages.batches.results(batch_id):
        cluster_id = item.custom_id
        if item.result.type == "succeeded":
            msg = item.result.message
            tool_block = next(
                (b for b in msg.content if b.type == "tool_use"), None
            )
            if tool_block is None:
                results.append(BatchItemResult(
                    cluster_id=cluster_id,
                    result=None,
                    usage=None,
                    error="succeeded but no tool_use block in response",
                ))
                continue
            try:
                assessment = ClusterAssessment(**tool_block.input)
            except Exception as exc:  # noqa: BLE001
                results.append(BatchItemResult(
                    cluster_id=cluster_id,
                    result=None,
                    usage=None,
                    error=f"ClusterAssessment parse error: {exc}",
                ))
                continue
            # Extract per-item usage. Anthropic provides this for succeeded batch
            # items, but guard explicitly: if the response shape lacks usage
            # attributes (e.g. API changes or unexpected result format), set
            # usage=None so no phantom zero-cost row is created downstream.
            raw_usage = getattr(msg, "usage", None)
            if (
                raw_usage is not None
                and hasattr(raw_usage, "input_tokens")
                and hasattr(raw_usage, "output_tokens")
            ):
                usage: Optional[LlmUsageInfo] = LlmUsageInfo(
                    model_name=model,
                    input_tokens=raw_usage.input_tokens,
                    output_tokens=raw_usage.output_tokens,
                    related_object_id=cluster_id,
                )
            else:
                usage = None
                logger.warning(
                    "assess_cluster_batch: per-item usage unavailable for "
                    "cluster_id=%s (msg.usage=%r); assessment persisted but "
                    "usage row will be skipped — no phantom zero-cost row",
                    cluster_id,
                    raw_usage,
                )
            results.append(BatchItemResult(
                cluster_id=cluster_id,
                result=assessment,
                usage=usage,
                error=None,
            ))
        else:
            # errored, canceled, or expired — usage unavailable; no phantom row
            error_type = item.result.type
            error_detail = str(getattr(item.result, "error", error_type))
            results.append(BatchItemResult(
                cluster_id=cluster_id,
                result=None,
                usage=None,
                error=f"{error_type}: {error_detail}",
            ))

    return batch_id, results, poll_duration_seconds
