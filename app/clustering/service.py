"""
Event clustering service.

Assigns a story to an event cluster based on extracted facts.
Deterministic, idempotent, no LLM.
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.clustering.rules import build_cluster_key
from app.models.event_cluster import EventCluster
from app.models.story import Story
from app.models.story_facts import StoryFacts

logger = logging.getLogger(__name__)


def cluster_story(
    db: Session, story: Story, facts: StoryFacts
) -> tuple[Optional[EventCluster], bool]:
    """
    Assign *story* to an event cluster using *facts*.

    Returns:
        (EventCluster, True)   — new cluster created and story assigned
        (EventCluster, False)  — story joined an existing cluster, or was already assigned
        (None, False)          — facts are insufficient; story not assigned

    Idempotent: calling twice on the same story returns the same cluster.
    """
    # Re-fetch from DB to avoid stale in-memory state.
    db.refresh(story)

    if story.event_cluster_id is not None:
        existing = db.get(EventCluster, story.event_cluster_id)
        logger.info(
            "cluster_story story=%s already assigned to cluster=%s",
            story.id, story.event_cluster_id,
        )
        return existing, False

    cluster_key = build_cluster_key(
        event_type=facts.event_type or "",
        company_names=facts.company_names or [],
        amount_text=facts.amount_text,
        currency=facts.currency,
    )
    if cluster_key is None:
        logger.info(
            "cluster_story story=%s event_type=%s — insufficient facts, skipping",
            story.id, facts.event_type,
        )
        return None, False

    existing_cluster = db.query(EventCluster).filter_by(cluster_key=cluster_key).first()
    if existing_cluster is not None:
        story.event_cluster_id = existing_cluster.id
        db.commit()
        logger.info(
            "cluster_story story=%s joined existing cluster=%s key=%s",
            story.id, existing_cluster.id, cluster_key,
        )
        return existing_cluster, False

    cluster = EventCluster(
        cluster_key=cluster_key,
        event_type=facts.event_type,
        representative_story_id=story.id,
    )
    db.add(cluster)
    db.flush()  # get cluster.id before updating story

    story.event_cluster_id = cluster.id
    db.commit()

    logger.info(
        "cluster_story story=%s created new cluster=%s key=%s",
        story.id, cluster.id, cluster_key,
    )
    return cluster, True
