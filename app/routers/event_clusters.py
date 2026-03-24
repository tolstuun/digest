import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.story import Story
from app.schemas.event_cluster import EventClusterOut
from app.schemas.event_cluster_assessment import EventClusterAssessmentOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/event-clusters", tags=["event-clusters"])


def _build_cluster_out(db: Session, cluster: EventCluster) -> EventClusterOut:
    story_ids = [
        s.id for s in db.query(Story).filter_by(event_cluster_id=cluster.id).all()
    ]
    return EventClusterOut(
        id=cluster.id,
        cluster_key=cluster.cluster_key,
        event_type=cluster.event_type,
        representative_story_id=cluster.representative_story_id,
        story_count=len(story_ids),
        story_ids=story_ids,
        created_at=cluster.created_at,
        updated_at=cluster.updated_at,
    )


@router.get("/", response_model=list[EventClusterOut])
def list_event_clusters(db: Session = Depends(get_db)) -> list[EventClusterOut]:
    clusters = db.query(EventCluster).order_by(EventCluster.created_at.desc()).all()
    return [_build_cluster_out(db, c) for c in clusters]


@router.get("/{cluster_id}", response_model=EventClusterOut)
def get_event_cluster(cluster_id: uuid.UUID, db: Session = Depends(get_db)) -> EventClusterOut:
    cluster = db.get(EventCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="Event cluster not found")
    return _build_cluster_out(db, cluster)


@router.get("/{cluster_id}/assessment", response_model=EventClusterAssessmentOut)
def get_cluster_assessment(
    cluster_id: uuid.UUID, db: Session = Depends(get_db)
) -> EventClusterAssessment:
    cluster = db.get(EventCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="Event cluster not found")
    assessment = (
        db.query(EventClusterAssessment).filter_by(event_cluster_id=cluster_id).first()
    )
    if assessment is None:
        raise HTTPException(
            status_code=404, detail="No assessment found for this cluster"
        )
    return assessment
