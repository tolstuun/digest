"""
Admin/dev endpoints for manual pipeline operations.
Not intended for public exposure — for operational use only.
"""
import logging
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.clustering.service import cluster_story
from app.config import settings
from app.database import get_db
from app.digest.service import MAX_ENTRIES_DEFAULT, SECTION_NAME, assemble_digest
from app.extraction.service import extract_story_facts
from app.ingestion.service import ingest_source
from app.models.digest_page import DigestPage
from app.models.digest_run import DigestRun
from app.models.event_cluster import EventCluster
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts
from app.normalization.service import normalize_raw_item
from app.publishing.service import publish_to_telegram
from app.rendering.service import render_digest_page
from app.schemas.digest_publication import DigestPublicationOut
from app.schemas.story_facts import StoryFactsOut
from app.scoring.service import assess_cluster

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/sources/{source_id}/ingest")
def trigger_ingest(source_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """
    Manually trigger ingestion for one source.
    Fetches, parses, and persists new raw items. Idempotent.
    Returns a summary: {source_id, fetched, new, skipped, error}.
    """
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    logger.info("Manual ingest triggered for source id=%s", source_id)
    result = ingest_source(db, source)
    return {"source_id": str(source_id), **result}


@router.post("/sources/{source_id}/normalize")
def trigger_normalize(source_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """
    Normalize all raw_items for one source into stories. Idempotent.
    Returns a summary: {source_id, total, new, skipped}.
    """
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    raw_items = (
        db.query(RawItem).filter_by(source_id=source_id).all()
    )

    new = 0
    skipped = 0
    for raw_item in raw_items:
        _, created = normalize_raw_item(db, raw_item)
        if created:
            new += 1
        else:
            skipped += 1

    logger.info(
        "Normalize done source=%s total=%d new=%d skipped=%d",
        source_id, len(raw_items), new, skipped,
    )
    return {
        "source_id": str(source_id),
        "total": len(raw_items),
        "new": new,
        "skipped": skipped,
    }


@router.post("/stories/{story_id}/extract-facts")
def trigger_extract_facts(story_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """
    Manually trigger LLM fact extraction for one story. Idempotent (upsert).
    Returns: {story_id, event_type, created}.
    """
    story = db.get(Story, story_id)
    if story is None:
        raise HTTPException(status_code=404, detail="Story not found")

    facts, created = extract_story_facts(db, story)
    logger.info("Manual extract-facts triggered for story id=%s created=%s", story_id, created)
    return {
        "story_id": str(facts.story_id),
        "event_type": facts.event_type,
        "created": created,
    }


@router.post("/stories/{story_id}/cluster-event")
def trigger_cluster_event(story_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """
    Assign one story to an event cluster based on its extracted facts.
    Deterministic and idempotent — no LLM.
    Returns: {story_id, clustered, cluster_id?, created?}.
    """
    story = db.get(Story, story_id)
    if story is None:
        raise HTTPException(status_code=404, detail="Story not found")

    facts = db.query(StoryFacts).filter_by(story_id=story_id).first()
    if facts is None:
        raise HTTPException(status_code=400, detail="Story has no extracted facts; run extract-facts first")

    cluster, created = cluster_story(db, story, facts)
    if cluster is None:
        logger.info("cluster-event story=%s — not clustered (insufficient facts)", story_id)
        return {"story_id": str(story_id), "clustered": False}

    logger.info(
        "cluster-event story=%s cluster=%s created=%s", story_id, cluster.id, created
    )
    return {
        "story_id": str(story_id),
        "clustered": True,
        "cluster_id": str(cluster.id),
        "created": created,
    }


@router.post("/event-clusters/{cluster_id}/assess")
def trigger_assess_cluster(
    cluster_id: uuid.UUID, db: Session = Depends(get_db)
) -> dict:
    """
    Manually trigger editorial assessment for one event cluster.
    Runs rule scoring + LLM editorial judgment. Idempotent (upsert).
    Returns: {cluster_id, primary_section, rule_score, llm_score, final_score, include_in_digest, created}.
    """
    cluster = db.get(EventCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="Event cluster not found")

    assessment, created = assess_cluster(db, cluster)
    logger.info(
        "assess cluster=%s final_score=%.3f include=%s created=%s",
        cluster_id, assessment.final_score or 0, assessment.include_in_digest, created,
    )
    return {
        "cluster_id": str(cluster_id),
        "primary_section": assessment.primary_section,
        "rule_score": assessment.rule_score,
        "llm_score": assessment.llm_score,
        "final_score": assessment.final_score,
        "include_in_digest": assessment.include_in_digest,
        "created": created,
    }


class AssembleDigestRequest(BaseModel):
    digest_date: date
    max_entries: Optional[int] = None


@router.post("/digests/assemble")
def trigger_assemble_digest(
    req: AssembleDigestRequest, db: Session = Depends(get_db)
) -> dict:
    """
    Manually trigger digest assembly for a given date and section (companies_business).
    Deterministic and idempotent — repeated calls for the same date delete and rebuild the run.
    Returns: {digest_run_id, digest_date, section_name, total_candidates, total_included, created}.
    """
    max_entries = req.max_entries if req.max_entries is not None else MAX_ENTRIES_DEFAULT
    run, entries, created = assemble_digest(
        db,
        digest_date=req.digest_date,
        section_name=SECTION_NAME,
        max_entries=max_entries,
    )
    logger.info(
        "assemble-digest date=%s section=%s included=%d created=%s",
        req.digest_date, SECTION_NAME, len(entries), created,
    )
    return {
        "digest_run_id": str(run.id),
        "digest_date": str(run.digest_date),
        "section_name": run.section_name,
        "total_candidates": run.total_candidate_clusters,
        "total_included": run.total_included_clusters,
        "created": created,
    }


@router.post("/digest-pages/{digest_page_id}/publish-telegram", response_model=DigestPublicationOut)
def trigger_publish_telegram(
    digest_page_id: uuid.UUID, db: Session = Depends(get_db)
) -> DigestPublicationOut:
    """
    Publish a rendered digest page to Telegram.
    Idempotent: repeated calls update the existing record and re-send.
    Requires telegram.enabled=true and bot_token/chat_id set in config.
    Returns the DigestPublication record.
    """
    page = db.get(DigestPage, digest_page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Digest page not found")

    try:
        pub, created = publish_to_telegram(db, page, settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(
        "publish-telegram page=%s status=%s created=%s",
        digest_page_id, pub.status, created,
    )
    return pub


@router.post("/digests/{digest_run_id}/render")
def trigger_render_digest(
    digest_run_id: uuid.UUID, db: Session = Depends(get_db)
) -> dict:
    """
    Manually trigger HTML rendering for one digest run.
    Idempotent: repeated calls update the existing page (stable page ID).
    Returns: {digest_page_id, digest_run_id, slug, rendered_at, created}.
    """
    run = db.get(DigestRun, digest_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Digest run not found")

    page, created = render_digest_page(db, run)
    logger.info(
        "render-digest run=%s slug=%s created=%s",
        digest_run_id, page.slug, created,
    )
    return {
        "digest_page_id": str(page.id),
        "digest_run_id": str(digest_run_id),
        "slug": page.slug,
        "rendered_at": page.rendered_at.isoformat() if page.rendered_at else None,
        "created": created,
    }
