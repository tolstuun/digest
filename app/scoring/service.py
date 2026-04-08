"""
Editorial scoring service.

Combines rule-based pre-score with LLM editorial judgment into a final score,
then upserts one EventClusterAssessment row per cluster.

Sync path:  assess_cluster() — one cluster, one Anthropic call, one DB row.
Batch path: assess_cluster_batch_run() — N clusters, one batch, N DB rows.

Both paths call _apply_assessment() to persist results and record LLM usage.
Usage is recorded only for succeeded items; errored batch items produce no rows.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.llm_usage.schemas import LlmUsageInfo
from app.llm_usage.service import record_usage
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts
from app.scoring.llm import assess_cluster_llm
from app.scoring.rules import compute_rule_score
from app.scoring.schemas import ClusterAssessment, ClusterInput

logger = logging.getLogger(__name__)

# Final score = weighted combination of rule and LLM scores.
# Rule score provides a stable deterministic floor; LLM provides editorial judgment.
_RULE_WEIGHT = 0.4
_LLM_WEIGHT = 0.6


def _build_cluster_input(
    db: Session,
    cluster: EventCluster,
) -> tuple[ClusterInput, float]:
    """
    Load all data needed to assess a cluster and return (ClusterInput, rule_score).

    Shared by the sync (assess_cluster) and batch (assess_cluster_batch_run) paths.
    """
    # Load linked stories
    stories = db.query(Story).filter_by(event_cluster_id=cluster.id).all()
    story_count = len(stories)

    # Load representative story and its facts
    rep_facts: StoryFacts | None = None
    rep_story: Story | None = None

    if cluster.representative_story_id:
        rep_story = db.get(Story, cluster.representative_story_id)
        rep_facts = (
            db.query(StoryFacts)
            .filter_by(story_id=cluster.representative_story_id)
            .first()
        )

    # Fall back to first story with facts if representative has none
    if rep_facts is None:
        for s in stories:
            f = db.query(StoryFacts).filter_by(story_id=s.id).first()
            if f:
                rep_facts = f
                rep_story = s
                break

    # Max source priority across linked stories
    max_priority = 0
    if stories:
        source_ids = [s.source_id for s in stories]
        result = (
            db.query(func.max(Source.priority))
            .filter(Source.id.in_(source_ids))
            .scalar()
        )
        max_priority = int(result or 0)

    # Compute rule score
    has_amount = bool(rep_facts and rep_facts.amount_text)
    has_currency = bool(rep_facts and rep_facts.currency)
    rule_score = compute_rule_score(
        event_type=cluster.event_type or "",
        story_count=story_count,
        has_amount=has_amount,
        has_currency=has_currency,
        max_source_priority=max_priority,
    )

    cluster_input = ClusterInput(
        cluster_id=str(cluster.id),
        event_type=cluster.event_type,
        story_count=story_count,
        company_names=(rep_facts.company_names or []) if rep_facts else [],
        amount_text=rep_facts.amount_text if rep_facts else None,
        currency=rep_facts.currency if rep_facts else None,
        canonical_summary_en=rep_facts.canonical_summary_en if rep_facts else None,
        canonical_summary_ru=rep_facts.canonical_summary_ru if rep_facts else None,
        representative_title=rep_story.title if rep_story else None,
    )

    return cluster_input, rule_score


def _apply_assessment(
    db: Session,
    cluster: EventCluster,
    llm_result: ClusterAssessment,
    llm_usage: Optional[LlmUsageInfo],
    rule_score: float,
    pipeline_run_id: Optional[uuid.UUID] = None,
    model_name: Optional[str] = None,
) -> tuple[EventClusterAssessment, bool]:
    """
    Persist ClusterAssessment to EventClusterAssessment and record LLM usage.

    Shared by the sync (assess_cluster) and batch (assess_cluster_batch_run) paths.
    Returns (EventClusterAssessment, created).

    llm_usage=None is permitted when per-item usage was unavailable in the batch
    response shape. In that case the assessment row is persisted normally but no
    LlmUsage row is created — no phantom zero-cost rows.
    Callers must NOT fabricate a zero-cost LlmUsageInfo to pass here.
    """
    final_score = round(_RULE_WEIGHT * rule_score + _LLM_WEIGHT * llm_result.llm_score, 4)

    existing = (
        db.query(EventClusterAssessment)
        .filter_by(event_cluster_id=cluster.id)
        .first()
    )
    created = existing is None

    if existing is None:
        assessment = EventClusterAssessment(event_cluster_id=cluster.id)
        db.add(assessment)
    else:
        assessment = existing

    assessment.primary_section = llm_result.primary_section
    assessment.rule_score = rule_score
    assessment.llm_score = llm_result.llm_score
    assessment.final_score = final_score
    assessment.include_in_digest = llm_result.include_in_digest
    assessment.why_it_matters_en = llm_result.why_it_matters_en
    assessment.why_it_matters_ru = llm_result.why_it_matters_ru
    assessment.editorial_notes = llm_result.editorial_notes
    assessment.model_name = model_name or settings.scoring_model
    assessment.raw_model_output = llm_result.model_dump()
    assessment.assessed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(assessment)

    if llm_usage is not None:
        record_usage(db, "assess", llm_usage, pipeline_run_id=pipeline_run_id)
    else:
        logger.warning(
            "apply_assessment cluster=%s: llm_usage is None — usage row skipped",
            cluster.id,
        )

    logger.info(
        "apply_assessment cluster=%s rule=%.3f llm=%.3f final=%.3f include=%s created=%s",
        cluster.id,
        rule_score,
        llm_result.llm_score,
        final_score,
        llm_result.include_in_digest,
        created,
    )
    return assessment, created


def assess_cluster(
    db: Session,
    cluster: EventCluster,
    pipeline_run_id: Optional[uuid.UUID] = None,
    output_language: Optional[str] = None,
) -> tuple[EventClusterAssessment, bool]:
    """
    Run full assessment for an event cluster and upsert the result.

    Steps:
      1. Load linked stories and representative story facts.
      2. Compute rule_score deterministically.
      3. Call assess_cluster_llm() for editorial judgment.
      4. Combine: final_score = 0.4 * rule_score + 0.6 * llm_score.
      5. Upsert to event_cluster_assessments (one row per cluster).

    Returns (EventClusterAssessment, created).
    Idempotent: repeated calls update the existing row.
    """
    cluster_input, rule_score = _build_cluster_input(db, cluster)
    lang = output_language or settings.digest.output_language
    llm_result, llm_usage = assess_cluster_llm(cluster_input, output_language=lang)
    return _apply_assessment(
        db, cluster, llm_result, llm_usage, rule_score,
        pipeline_run_id=pipeline_run_id,
    )


def assess_cluster_batch_run(
    db: Session,
    clusters: list[EventCluster],
    cfg: Settings,
    pipeline_run_id: Optional[uuid.UUID] = None,
    output_language: Optional[str] = None,
) -> dict:
    """
    Run batch assessment for a list of clusters via Anthropic Message Batches.

    clusters must be the final scoped shortlist — already gate-filtered,
    date-scoped, and cap-limited by the caller.

    Returns a dict with batch observability fields:
      mode, batch_id, submitted, succeeded, failed, timed_out,
      poll_duration_seconds.

    Raises BatchTimeoutError if the batch does not complete within
    cfg.llm.batch_timeout_minutes. There is NO sync fallback — the caller
    must treat BatchTimeoutError as a hard step failure.

    Usage is recorded (with pipeline_run_id) for every succeeded item using
    the real per-item token counts from the batch result. Errored/canceled/
    expired items produce no LlmUsage rows — not zero-cost phantom rows.
    """
    from app.scoring.batch import assess_cluster_batch

    lang = output_language or cfg.digest.output_language
    cluster_map = {str(c.id): c for c in clusters}

    cluster_inputs = []
    rule_scores: dict[str, float] = {}
    for cluster in clusters:
        cluster_input, rule_score = _build_cluster_input(db, cluster)
        cluster_inputs.append((str(cluster.id), cluster_input))
        rule_scores[str(cluster.id)] = rule_score

    batch_id, results, poll_duration_seconds = assess_cluster_batch(
        cluster_inputs=cluster_inputs,
        api_key=cfg.llm.api_key,
        model=cfg.llm.model_scoring,
        output_language=lang,
        poll_interval_seconds=cfg.llm.batch_poll_interval_seconds,
        timeout_minutes=cfg.llm.batch_timeout_minutes,
    )

    succeeded = failed = missing_usage = 0
    for item in results:
        if item.result is None:
            # errored/canceled/expired — usage is None; no row recorded
            failed += 1
            logger.warning(
                "batch_assess item failed cluster=%s: %s", item.cluster_id, item.error
            )
            continue
        cluster = cluster_map.get(item.cluster_id)
        if cluster is None:
            logger.error(
                "batch_assess result cluster_id=%s not in cluster_map", item.cluster_id
            )
            failed += 1
            continue
        if item.usage is None:
            # Assessment parsed OK but per-item usage was absent from the response
            # shape. Noted explicitly — no phantom zero-cost row will be created.
            missing_usage += 1
            logger.warning(
                "batch_assess cluster=%s: assessment available but usage unavailable; "
                "persisting assessment without usage row",
                item.cluster_id,
            )
        rule_score = rule_scores[item.cluster_id]
        # Persist each item independently so a single DB error does not abort the
        # rest. usage=None is forwarded; _apply_assessment skips record_usage then.
        try:
            _apply_assessment(
                db, cluster, item.result, item.usage, rule_score,
                pipeline_run_id=pipeline_run_id,
                model_name=cfg.llm.model_scoring,
            )
            succeeded += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.error(
                "batch_assess: failed to persist cluster=%s: %s", item.cluster_id, exc
            )

    result: dict = {
        "mode": "batch",
        "batch_id": batch_id,
        "submitted": len(clusters),
        "succeeded": succeeded,
        "failed": failed,
        "timed_out": False,
        "poll_duration_seconds": poll_duration_seconds,
    }
    if missing_usage:
        result["missing_usage"] = missing_usage
    return result
