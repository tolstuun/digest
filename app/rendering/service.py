"""
Digest rendering service.

render_digest_page() loads a DigestRun's entries, calls render_digest_html(),
and upserts a DigestPage row.

Idempotent policy: upsert — if a DigestPage already exists for the run,
update its html_content, slug, title, and rendered_at in place.
This means the page ID stays stable across re-renders.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings as _settings
from app.models.digest_entry import DigestEntry
from app.models.digest_page import DigestPage
from app.models.digest_run import DigestRun
from app.rendering.html import make_slug, make_title, render_digest_html

logger = logging.getLogger(__name__)


def render_digest_page(
    db: Session, run: DigestRun
) -> tuple[DigestPage, bool]:
    """
    Render HTML for a digest run and upsert the DigestPage.

    Idempotent: if a page already exists for this run, it is updated.
    The page ID remains stable across re-renders.

    Returns (digest_page, created).
    created=True means a new page row was inserted; False means it was updated.
    """
    entries = (
        db.query(DigestEntry)
        .filter_by(digest_run_id=run.id)
        .order_by(DigestEntry.rank)
        .all()
    )

    output_language = _settings.digest.output_language
    html = render_digest_html(run, entries, output_language=output_language)
    slug = make_slug(run)
    title = make_title(run)
    now = datetime.now(timezone.utc)

    existing = db.query(DigestPage).filter_by(digest_run_id=run.id).first()
    created = existing is None

    if existing is None:
        page = DigestPage(
            digest_run_id=run.id,
            slug=slug,
            title=title,
            html_content=html,
            rendered_at=now,
        )
        db.add(page)
    else:
        page = existing
        page.slug = slug
        page.title = title
        page.html_content = html
        page.rendered_at = now

    db.commit()
    db.refresh(page)

    logger.info(
        "render_digest_page run=%s slug=%s created=%s",
        run.id,
        slug,
        created,
    )
    return page, created
