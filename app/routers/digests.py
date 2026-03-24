import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.digest_entry import DigestEntry
from app.models.digest_run import DigestRun
from app.schemas.digest import DigestRunDetail, DigestRunOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/digests", tags=["digests"])


@router.get("/", response_model=list[DigestRunOut])
def list_digests(db: Session = Depends(get_db)) -> list[DigestRunOut]:
    runs = db.query(DigestRun).order_by(DigestRun.digest_date.desc()).all()
    return runs


@router.get("/{digest_run_id}", response_model=DigestRunDetail)
def get_digest(digest_run_id: uuid.UUID, db: Session = Depends(get_db)) -> DigestRunDetail:
    run = db.get(DigestRun, digest_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Digest run not found")
    entries = (
        db.query(DigestEntry)
        .filter_by(digest_run_id=digest_run_id)
        .order_by(DigestEntry.rank)
        .all()
    )
    return DigestRunDetail(
        id=run.id,
        digest_date=run.digest_date,
        section_name=run.section_name,
        status=run.status,
        total_candidate_clusters=run.total_candidate_clusters,
        total_included_clusters=run.total_included_clusters,
        generated_at=run.generated_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        entries=entries,
    )
