import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.source import Source
from app.schemas.source import SourceCreate, SourceOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db)) -> list[Source]:
    return db.query(Source).order_by(Source.created_at.desc()).all()


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
