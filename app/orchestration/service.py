"""
Daily pipeline orchestration service.

run_daily_pipeline() executes all pipeline stages in order for a given date,
persisting one PipelineRun row and one PipelineRunStep per stage.

Rerun policy:
  - Reruns are allowed for the same run_date.
  - Each call creates a NEW PipelineRun row (run history is preserved).
  - Stage-level idempotency is relied upon — each stage service handles
    duplicate detection (upserts, content_hash checks, etc.).

Failure policy:
  - On any hard step failure, the step is marked "failed" and the run is
    marked "failed". Subsequent steps are NOT executed.
  - Soft misses (e.g. no stories to extract) are "success" with empty details.

Step order (fixed):
  1. ingest          — fetch all enabled RSS sources
  2. normalize       — normalize all raw items that lack a story
  3. extract_facts   — LLM fact extraction for stories without facts
  4. cluster_event   — cluster stories with facts but no cluster assigned
  5. assess          — score clusters without an assessment
  6. assemble_digest — assemble digest for run_date + companies_business
  7. render_digest   — render HTML for the assembled run
  8. publish_telegram — send to Telegram if enabled
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.clustering.service import cluster_story
from app.config import Settings
from app.digest.service import MAX_ENTRIES_DEFAULT, SECTION_NAME, assemble_digest
from app.extraction.service import extract_story_facts
from app.ingestion.service import ingest_source
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.pipeline_run import PipelineRun
from app.models.pipeline_run_step import PipelineRunStep
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts
from app.normalization.service import normalize_raw_item
from app.publishing.service import publish_to_telegram
from app.rendering.service import render_digest_page
from app.scoring.service import assess_cluster

logger = logging.getLogger(__name__)

STEP_NAMES = [
    "ingest",
    "normalize",
    "extract_facts",
    "cluster_event",
    "assess",
    "assemble_digest",
    "render_digest",
    "publish_telegram",
]


# ── helpers ───────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _start_step(db: Session, run: PipelineRun, step_name: str) -> PipelineRunStep:
    step = PipelineRunStep(
        pipeline_run_id=run.id,
        step_name=step_name,
        status="running",
        started_at=_now(),
    )
    db.add(step)
    db.commit()
    db.refresh(step)
    logger.info("pipeline_run=%s step=%s started", run.id, step_name)
    return step


def _finish_step(
    db: Session,
    step: PipelineRunStep,
    *,
    status: str,
    details: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    step.status = status
    step.finished_at = _now()
    step.details_json = details
    step.error_message = error
    db.commit()
    logger.info(
        "pipeline_run=%s step=%s status=%s",
        step.pipeline_run_id, step.step_name, status,
    )


def _finish_run(
    db: Session,
    run: PipelineRun,
    *,
    status: str,
    error: Optional[str] = None,
) -> None:
    run.status = status
    run.finished_at = _now()
    run.error_message = error
    db.commit()
    logger.info("pipeline_run=%s status=%s", run.id, status)


# ── individual step executors ─────────────────────────────────────────────────


def _run_ingest(db: Session) -> dict:
    sources = db.query(Source).filter_by(enabled=True).all()
    totals: dict = {"sources": len(sources), "new": 0, "skipped": 0, "errors": 0}
    for source in sources:
        if source.type != "rss":
            continue
        result = ingest_source(db, source)
        totals["new"] += result.get("new", 0)
        totals["skipped"] += result.get("skipped", 0)
        if result.get("error"):
            totals["errors"] += 1
    return totals


def _run_normalize(db: Session) -> dict:
    # Raw items that don't yet have a corresponding story
    raw_items = (
        db.query(RawItem)
        .outerjoin(Story, RawItem.id == Story.raw_item_id)
        .filter(Story.id.is_(None))
        .all()
    )
    new = skipped = 0
    for raw_item in raw_items:
        _, created = normalize_raw_item(db, raw_item)
        if created:
            new += 1
        else:
            skipped += 1
    return {"total": len(raw_items), "new": new, "skipped": skipped}


def _run_extract_facts(db: Session) -> dict:
    # Stories that don't yet have a StoryFacts row
    stories = (
        db.query(Story)
        .outerjoin(StoryFacts, Story.id == StoryFacts.story_id)
        .filter(StoryFacts.id.is_(None))
        .all()
    )
    new = updated = errors = 0
    for story in stories:
        try:
            _, created = extract_story_facts(db, story)
            if created:
                new += 1
            else:
                updated += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning("extract_facts failed story=%s: %s", story.id, exc)
    return {"total": len(stories), "new": new, "updated": updated, "errors": errors}


def _run_cluster_event(db: Session) -> dict:
    # Stories with facts but no cluster assigned yet
    stories = (
        db.query(Story)
        .join(StoryFacts, Story.id == StoryFacts.story_id)
        .filter(Story.event_cluster_id.is_(None))
        .all()
    )
    clustered = not_clustered = 0
    for story in stories:
        facts = db.query(StoryFacts).filter_by(story_id=story.id).first()
        if facts is None:
            continue
        cluster, _ = cluster_story(db, story, facts)
        if cluster is not None:
            clustered += 1
        else:
            not_clustered += 1
    return {"total": len(stories), "clustered": clustered, "not_clustered": not_clustered}


def _run_assess(db: Session) -> dict:
    # Clusters without an assessment row
    clusters = (
        db.query(EventCluster)
        .outerjoin(
            EventClusterAssessment,
            EventCluster.id == EventClusterAssessment.event_cluster_id,
        )
        .filter(EventClusterAssessment.id.is_(None))
        .all()
    )
    assessed = errors = 0
    for cluster in clusters:
        try:
            assess_cluster(db, cluster)
            assessed += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning("assess failed cluster=%s: %s", cluster.id, exc)
    return {"total": len(clusters), "assessed": assessed, "errors": errors}


def _run_assemble_digest(db: Session, run_date: date) -> dict:
    run, entries, created = assemble_digest(
        db,
        digest_date=run_date,
        section_name=SECTION_NAME,
        max_entries=MAX_ENTRIES_DEFAULT,
    )
    return {
        "digest_run_id": str(run.id),
        "total_included": len(entries),
        "created": created,
    }


def _run_render_digest(db: Session, run_date: date) -> dict:
    from app.models.digest_run import DigestRun

    run = (
        db.query(DigestRun)
        .filter_by(digest_date=run_date, section_name=SECTION_NAME)
        .first()
    )
    if run is None:
        return {"skipped": True, "reason": "no digest run found for date"}
    page, created = render_digest_page(db, run)
    return {
        "digest_page_id": str(page.id),
        "slug": page.slug,
        "created": created,
    }


def _run_publish_telegram(
    db: Session, run_date: date, cfg: Settings
) -> dict:
    if not cfg.telegram.enabled:
        return {"skipped": True, "reason": "telegram.enabled=false"}

    from app.models.digest_page import DigestPage
    from app.models.digest_run import DigestRun

    run = (
        db.query(DigestRun)
        .filter_by(digest_date=run_date, section_name=SECTION_NAME)
        .first()
    )
    if run is None:
        return {"skipped": True, "reason": "no digest run for date"}

    page = db.query(DigestPage).filter_by(digest_run_id=run.id).first()
    if page is None:
        return {"skipped": True, "reason": "no digest page for run"}

    pub, created = publish_to_telegram(db, page, cfg)
    return {
        "digest_publication_id": str(pub.id),
        "message_id": pub.provider_message_id,
        "created": created,
    }


# ── main orchestrator ─────────────────────────────────────────────────────────


def run_daily_pipeline(
    db: Session,
    run_date: date,
    trigger_type: str = "scheduled",
    publish_telegram: Optional[bool] = None,
    cfg: Optional[Settings] = None,
) -> dict:
    """
    Run the full daily pipeline for run_date.

    Creates a new PipelineRun row each call (run history is preserved).
    Relies on per-stage idempotency for safe reruns.

    publish_telegram:
      None  → use cfg.scheduler.publish_telegram_by_default (or False if cfg is None)
      True  → publish regardless of scheduler config
      False → skip publishing regardless of scheduler config

    Returns a summary dict with run_id, status, step results, and key IDs.
    """
    if cfg is None:
        from app.config import settings as _settings
        cfg = _settings

    # Resolve publish flag
    if publish_telegram is None:
        should_publish = cfg.scheduler.publish_telegram_by_default
    else:
        should_publish = publish_telegram

    now = _now()
    run = PipelineRun(
        run_date=run_date,
        trigger_type=trigger_type,
        status="running",
        started_at=now,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    logger.info(
        "pipeline_run=%s started run_date=%s trigger=%s",
        run.id, run_date, trigger_type,
    )

    step_results: dict[str, dict] = {}
    failed_step: Optional[str] = None

    # Map step names to their executors
    step_executors = [
        ("ingest",           lambda: _run_ingest(db)),
        ("normalize",        lambda: _run_normalize(db)),
        ("extract_facts",    lambda: _run_extract_facts(db)),
        ("cluster_event",    lambda: _run_cluster_event(db)),
        ("assess",           lambda: _run_assess(db)),
        ("assemble_digest",  lambda: _run_assemble_digest(db, run_date)),
        ("render_digest",    lambda: _run_render_digest(db, run_date)),
        ("publish_telegram", lambda: (
            _run_publish_telegram(db, run_date, cfg)
            if should_publish
            else {"skipped": True, "reason": "publish_telegram=False"}
        )),
    ]

    for step_name, executor in step_executors:
        step = _start_step(db, run, step_name)
        try:
            details = executor()
            _finish_step(db, step, status="success", details=details)
            step_results[step_name] = details
        except Exception as exc:  # noqa: BLE001
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "pipeline_run=%s step=%s FAILED: %s", run.id, step_name, error_msg
            )
            _finish_step(db, step, status="failed", error=error_msg)
            step_results[step_name] = {"error": error_msg}
            failed_step = step_name
            _finish_run(db, run, status="failed", error=error_msg)
            break
    else:
        _finish_run(db, run, status="success")

    # Build summary
    summary: dict = {
        "pipeline_run_id": str(run.id),
        "run_date": str(run_date),
        "status": run.status,
        "trigger_type": trigger_type,
        "failed_step": failed_step,
        "step_results": step_results,
    }

    # Extract key IDs from step results for convenience
    assemble_result = step_results.get("assemble_digest", {})
    if "digest_run_id" in assemble_result:
        summary["digest_run_id"] = assemble_result["digest_run_id"]

    render_result = step_results.get("render_digest", {})
    if "digest_page_id" in render_result:
        summary["digest_page_id"] = render_result["digest_page_id"]

    publish_result = step_results.get("publish_telegram", {})
    if "digest_publication_id" in publish_result:
        summary["digest_publication_id"] = publish_result["digest_publication_id"]

    return summary
