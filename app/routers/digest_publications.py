import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.digest_publication import DigestPublication
from app.schemas.digest_publication import DigestPublicationOut

router = APIRouter(prefix="/digest-publications", tags=["digest-publications"])


@router.get("/", response_model=List[DigestPublicationOut])
def list_digest_publications(db: Session = Depends(get_db)) -> List[DigestPublication]:
    """List all digest publications."""
    return (
        db.query(DigestPublication)
        .order_by(DigestPublication.published_at.desc())
        .all()
    )


@router.get("/{publication_id}", response_model=DigestPublicationOut)
def get_digest_publication(
    publication_id: uuid.UUID, db: Session = Depends(get_db)
) -> DigestPublication:
    """Get a single digest publication by ID."""
    pub = db.get(DigestPublication, publication_id)
    if pub is None:
        raise HTTPException(status_code=404, detail="Digest publication not found")
    return pub
