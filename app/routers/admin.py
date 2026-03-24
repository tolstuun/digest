"""
Admin/dev endpoints for manual pipeline operations.
Not intended for public exposure — for operational use only.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.clustering.service import cluster_story
from app.database import get_db
from app.extraction.service import extract_story_facts
from app.ingestion.service import ingest_source
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts
from app.normalization.service import normalize_raw_item
from app.schemas.story_facts import StoryFactsOut

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
