"""
Internal ops/admin UI.

Serves server-rendered HTML pages for operational inspection and manual
pipeline triggering. No auth (internal tool). No JS/SPA.

Routes:
  GET  /ui/                            dashboard
  GET  /ui/sources                     sources list
  POST /ui/sources/{id}/ingest         trigger ingest, redirect back
  POST /ui/sources/{id}/normalize      trigger normalize, redirect back
  GET  /ui/event-clusters              event clusters list
  POST /ui/event-clusters/{id}/assess  trigger assessment, redirect back
  GET  /ui/digests                     digest runs list
  POST /ui/digests/assemble            assemble for a date, redirect back
  POST /ui/digests/{id}/render         render digest page, redirect back
  GET  /ui/config                      read-only config view
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.clustering.service import cluster_story
from app.config import settings
from app.database import get_db
from app.digest.service import MAX_ENTRIES_DEFAULT, SECTION_NAME, assemble_digest
from app.ingestion.service import ingest_source
from app.models.digest_page import DigestPage
from app.models.digest_run import DigestRun
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts
from app.normalization.service import normalize_raw_item
from app.rendering.service import render_digest_page
from app.scoring.service import assess_cluster

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# ── Jinja2 custom filter ───────────────────────────────────────────────────────


def _mask_secret(value: str, visible: int = 4) -> str:
    """Show first `visible` chars then stars; blank string becomes '[empty]'."""
    if not value:
        return "[empty]"
    if len(value) <= visible:
        return "*" * len(value)
    return value[:visible] + "*" * min(len(value) - visible, 12)


templates.env.filters["mask_secret"] = _mask_secret

# ── router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/ui", tags=["ui"])


def _redirect(path: str, flash_level: str = "ok", flash_message: str = "") -> RedirectResponse:
    """Redirect to path; flash messages are passed as query params."""
    sep = "&" if "?" in path else "?"
    if flash_message:
        from urllib.parse import quote
        path = f"{path}{sep}flash_level={flash_level}&flash_msg={quote(flash_message)}"
    return RedirectResponse(url=path, status_code=303)


def _flash(request: Request) -> Optional[dict]:
    level = request.query_params.get("flash_level")
    msg = request.query_params.get("flash_msg")
    if level and msg:
        return {"level": level, "message": msg}
    return None


# ── dashboard ─────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
def ui_dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    counts = {
        "sources": db.query(Source).count(),
        "raw_items": db.query(RawItem).count(),
        "stories": db.query(Story).count(),
        "event_clusters": db.query(EventCluster).count(),
        "digest_runs": db.query(DigestRun).count(),
        "digest_pages": db.query(DigestPage).count(),
    }
    recent_errors = (
        db.query(Source)
        .filter(Source.last_error.isnot(None))
        .order_by(Source.updated_at.desc())
        .limit(10)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "ui/dashboard.html",
        {
            "active": "dashboard",
            "counts": counts,
            "recent_errors": recent_errors,
            "flash": _flash(request),
        },
    )


# ── sources ───────────────────────────────────────────────────────────────────


@router.get("/sources", response_class=HTMLResponse)
def ui_sources(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    sources = db.query(Source).order_by(Source.priority.desc(), Source.name).all()
    return templates.TemplateResponse(
        request,
        "ui/sources.html",
        {
            "active": "sources",
            "sources": sources,
            "flash": _flash(request),
        },
    )


@router.post("/sources/{source_id}/ingest")
def ui_ingest_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> RedirectResponse:
    source = db.get(Source, source_id)
    if source is None:
        return _redirect("/ui/sources", "err", "Source not found")
    try:
        result = ingest_source(db, source)
        msg = f"Ingest done: {result.get('new', 0)} new, {result.get('skipped', 0)} skipped"
        logger.info("UI ingest source=%s %s", source_id, result)
        return _redirect("/ui/sources", "ok", msg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("UI ingest failed source=%s", source_id)
        return _redirect("/ui/sources", "err", f"Ingest failed: {exc}")


@router.post("/sources/{source_id}/normalize")
def ui_normalize_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> RedirectResponse:
    source = db.get(Source, source_id)
    if source is None:
        return _redirect("/ui/sources", "err", "Source not found")
    try:
        raw_items = db.query(RawItem).filter_by(source_id=source_id).all()
        new = skipped = 0
        for item in raw_items:
            _, created = normalize_raw_item(db, item)
            if created:
                new += 1
            else:
                skipped += 1
        msg = f"Normalize done: {new} new, {skipped} skipped"
        logger.info("UI normalize source=%s new=%d skipped=%d", source_id, new, skipped)
        return _redirect("/ui/sources", "ok", msg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("UI normalize failed source=%s", source_id)
        return _redirect("/ui/sources", "err", f"Normalize failed: {exc}")


# ── event clusters ────────────────────────────────────────────────────────────


@router.get("/event-clusters", response_class=HTMLResponse)
def ui_event_clusters(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    clusters = db.query(EventCluster).order_by(EventCluster.created_at.desc()).all()

    # Build story counts per cluster
    from sqlalchemy import func
    story_counts: dict[uuid.UUID, int] = {
        row[0]: row[1]
        for row in db.query(Story.event_cluster_id, func.count(Story.id))
        .filter(Story.event_cluster_id.isnot(None))
        .group_by(Story.event_cluster_id)
        .all()
    }

    # Load assessments
    assessments: dict[uuid.UUID, EventClusterAssessment] = {
        a.event_cluster_id: a
        for a in db.query(EventClusterAssessment).all()
    }

    rows = []
    for c in clusters:
        a = assessments.get(c.id)
        rows.append({
            "id": c.id,
            "event_type": c.event_type,
            "representative_story_id": c.representative_story_id,
            "story_count": story_counts.get(c.id, 0),
            "assessed": a is not None,
            "primary_section": a.primary_section if a else None,
            "final_score": a.final_score if a else None,
            "include_in_digest": a.include_in_digest if a else None,
        })

    return templates.TemplateResponse(
        request,
        "ui/event_clusters.html",
        {
            "active": "event-clusters",
            "clusters": rows,
            "flash": _flash(request),
        },
    )


@router.post("/event-clusters/{cluster_id}/assess")
def ui_assess_cluster(cluster_id: uuid.UUID, db: Session = Depends(get_db)) -> RedirectResponse:
    cluster = db.get(EventCluster, cluster_id)
    if cluster is None:
        return _redirect("/ui/event-clusters", "err", "Cluster not found")
    try:
        assessment, created = assess_cluster(db, cluster)
        msg = (
            f"Assessment done: section={assessment.primary_section} "
            f"score={assessment.final_score:.3f} include={assessment.include_in_digest}"
        )
        logger.info("UI assess cluster=%s created=%s", cluster_id, created)
        return _redirect("/ui/event-clusters", "ok", msg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("UI assess failed cluster=%s", cluster_id)
        return _redirect("/ui/event-clusters", "err", f"Assessment failed: {exc}")


# ── digests ───────────────────────────────────────────────────────────────────


@router.get("/digests", response_class=HTMLResponse)
def ui_digests(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    runs = db.query(DigestRun).order_by(DigestRun.digest_date.desc()).all()

    pages: dict[uuid.UUID, DigestPage] = {
        p.digest_run_id: p for p in db.query(DigestPage).all()
    }

    rows = []
    for r in runs:
        p = pages.get(r.id)
        rows.append({
            "id": r.id,
            "digest_date": r.digest_date,
            "section_name": r.section_name,
            "status": r.status,
            "total_candidate_clusters": r.total_candidate_clusters,
            "total_included_clusters": r.total_included_clusters,
            "generated_at": r.generated_at,
            "page_slug": p.slug if p else None,
        })

    return templates.TemplateResponse(
        request,
        "ui/digests.html",
        {
            "active": "digests",
            "runs": rows,
            "today": date.today().isoformat(),
            "flash": _flash(request),
        },
    )


@router.post("/digests/assemble")
def ui_assemble_digest(
    digest_date: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        parsed_date = date.fromisoformat(digest_date)
    except ValueError:
        return _redirect("/ui/digests", "err", f"Invalid date: {digest_date}")
    try:
        run, entries, created = assemble_digest(
            db, digest_date=parsed_date, section_name=SECTION_NAME,
            max_entries=MAX_ENTRIES_DEFAULT,
        )
        msg = (
            f"Assembled {parsed_date}: {run.total_included_clusters} entries "
            f"({'new' if created else 'rebuilt'})"
        )
        logger.info("UI assemble date=%s included=%d", parsed_date, len(entries))
        return _redirect("/ui/digests", "ok", msg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("UI assemble failed date=%s", digest_date)
        return _redirect("/ui/digests", "err", f"Assemble failed: {exc}")


@router.post("/digests/{run_id}/render")
def ui_render_digest(run_id: uuid.UUID, db: Session = Depends(get_db)) -> RedirectResponse:
    run = db.get(DigestRun, run_id)
    if run is None:
        return _redirect("/ui/digests", "err", "Digest run not found")
    try:
        page, created = render_digest_page(db, run)
        msg = f"Rendered {page.slug} ({'new' if created else 'updated'})"
        logger.info("UI render run=%s slug=%s", run_id, page.slug)
        return _redirect("/ui/digests", "ok", msg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("UI render failed run=%s", run_id)
        return _redirect("/ui/digests", "err", f"Render failed: {exc}")


# ── config ────────────────────────────────────────────────────────────────────


@router.get("/config", response_class=HTMLResponse)
def ui_config(request: Request) -> HTMLResponse:
    config_file_exists = Path(settings.config_path).exists()
    return templates.TemplateResponse(
        request,
        "ui/config.html",
        {
            "active": "config",
            "cfg": settings,
            "config_path": settings.config_path,
            "config_file_exists": config_file_exists,
            "flash": _flash(request),
        },
    )
