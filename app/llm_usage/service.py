"""
LLM usage recording service.

record_usage() persists one LlmUsage row for every LLM boundary call.
Call it from extraction, scoring, and digest-writer services after each LLM call.
"""
import logging
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.llm_usage.cost import estimate_cost_usd
from app.llm_usage.schemas import LlmUsageInfo
from app.models.llm_usage import LlmUsage

logger = logging.getLogger(__name__)


def record_usage(
    db: Session,
    stage_name: str,
    usage: LlmUsageInfo,
) -> LlmUsage:
    """
    Persist one LlmUsage row and return it.

    Errors are caught and logged — usage recording must never fail the pipeline.
    """
    try:
        related_id: Optional[uuid.UUID] = None
        if usage.related_object_id:
            try:
                related_id = uuid.UUID(usage.related_object_id)
            except ValueError:
                pass

        cost = estimate_cost_usd(usage.model_name, usage.input_tokens, usage.output_tokens)

        row = LlmUsage(
            stage_name=stage_name,
            model_name=usage.model_name,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            estimated_cost_usd=cost,
            related_object_id=related_id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.debug(
            "llm_usage stage=%s model=%s in=%d out=%d cost=%s related=%s",
            stage_name, usage.model_name, usage.input_tokens,
            usage.output_tokens, cost, related_id,
        )
        return row
    except Exception as exc:  # noqa: BLE001
        logger.warning("record_usage failed (non-fatal): %s", exc)
        db.rollback()
        # Return a dummy row so callers don't need to handle None
        return LlmUsage(
            stage_name=stage_name,
            model_name=usage.model_name,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
