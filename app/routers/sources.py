import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.source import Source, _utcnow
from app.schemas.source import SourceCreate, SourceOut, SourcePatch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db)) -> list[Source]:
    return db.query(Source).order_by(Source.created_at.desc()).all()


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.post("/", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def create_source(data: SourceCreate, db: Session = Depends(get_db)) -> Source:
    source = Source(**data.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    logger.info(
        "Created source id=%s name=%r type=%s", source.id, source.name, source.type
    )
    return source


@router.patch("/{source_id}", response_model=SourceOut)
def patch_source(
    source_id: uuid.UUID, data: SourcePatch, db: Session = Depends(get_db)
) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(source, field, value)

    source.updated_at = _utcnow()
    db.commit()
    db.refresh(source)
    logger.info("Patched source id=%s fields=%s", source.id, list(updates.keys()))
    return source
