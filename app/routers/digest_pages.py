import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.digest_page import DigestPage
from app.schemas.digest_page import DigestPageOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/digest-pages", tags=["digest-pages"])


@router.get("/", response_model=list[DigestPageOut])
def list_digest_pages(db: Session = Depends(get_db)) -> list[DigestPageOut]:
    """List all rendered digest pages (metadata only; no html_content)."""
    pages = db.query(DigestPage).order_by(DigestPage.rendered_at.desc()).all()
    return pages


@router.get("/{slug}", response_class=HTMLResponse)
def get_digest_page_by_slug(slug: str, db: Session = Depends(get_db)) -> HTMLResponse:
    """Return the rendered HTML page for the given slug."""
    page = db.query(DigestPage).filter_by(slug=slug).first()
    if page is None:
        raise HTTPException(status_code=404, detail="Digest page not found")
    return HTMLResponse(content=page.html_content, status_code=200)
