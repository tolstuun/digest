"""
Tests for Phase 4B: HTML rendering.

No LLM calls. No network calls. Rendering is fully deterministic.
"""
import hashlib
import uuid
from datetime import date, datetime, timezone

import pytest

from app.models.digest_entry import DigestEntry
from app.models.digest_page import DigestPage
from app.models.digest_run import DigestRun
from app.rendering.html import make_slug, make_title, render_digest_html
from app.rendering.service import render_digest_page


# ── helpers ───────────────────────────────────────────────────────────────────

TARGET_DATE = date(2026, 3, 24)
SECTION = "companies_business"


def _make_run(
    db,
    digest_date: date = TARGET_DATE,
    section_name: str = SECTION,
    total_included: int = 1,
    generated_at: datetime | None = None,
) -> DigestRun:
    run = DigestRun(
        digest_date=digest_date,
        section_name=section_name,
        status="assembled" if total_included > 0 else "empty",
        total_candidate_clusters=total_included,
        total_included_clusters=total_included,
        generated_at=generated_at or datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _make_entry(
    db,
    run: DigestRun,
    rank: int = 1,
    title: str = "Acme raises $50M",
    final_score: float = 0.85,
    summary_en: str = "Acme raised $50M in Series B.",
    summary_ru: str = "Компания Acme привлекла $50M.",
    why_en: str = "Significant funding for a cybersecurity startup.",
    why_ru: str = "Значительное финансирование для стартапа.",
) -> DigestEntry:
    entry = DigestEntry(
        digest_run_id=run.id,
        event_cluster_id=None,
        rank=rank,
        final_score=final_score,
        title=title,
        canonical_summary_en=summary_en,
        canonical_summary_ru=summary_ru,
        why_it_matters_en=why_en,
        why_it_matters_ru=why_ru,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def _make_run_with_entries(
    db, n: int = 2, date: date = TARGET_DATE, section: str = SECTION
) -> tuple[DigestRun, list[DigestEntry]]:
    run = _make_run(db, digest_date=date, section_name=section, total_included=n)
    entries = [
        _make_entry(
            db, run,
            rank=i + 1,
            title=f"Story #{i + 1}",
            final_score=round(0.9 - i * 0.1, 2),
            summary_en=f"Summary EN #{i + 1}",
            summary_ru=f"Summary RU #{i + 1}",
            why_en=f"Why EN #{i + 1}",
            why_ru=f"Why RU #{i + 1}",
        )
        for i in range(n)
    ]
    return run, entries


# ── make_slug (pure) ──────────────────────────────────────────────────────────

def test_make_slug_format(db):
    run = _make_run(db)
    assert make_slug(run) == "2026-03-24-companies-business"


def test_make_slug_replaces_underscores(db):
    run = _make_run(db, section_name="companies_business")
    slug = make_slug(run)
    assert "_" not in slug
    assert "companies-business" in slug


def test_make_slug_different_dates_differ(db):
    run1 = _make_run(db, digest_date=date(2026, 3, 24))
    run2 = _make_run(db, digest_date=date(2026, 3, 25), section_name="incidents")
    assert make_slug(run1) != make_slug(run2)


# ── make_title (pure) ─────────────────────────────────────────────────────────

def test_make_title_contains_date(db):
    run = _make_run(db)
    assert "2026-03-24" in make_title(run)


def test_make_title_contains_section(db):
    run = _make_run(db)
    title = make_title(run)
    assert "Companies Business" in title


# ── render_digest_html (pure) ─────────────────────────────────────────────────

def test_render_html_returns_doctype(db):
    run = _make_run(db)
    html = render_digest_html(run, [])
    assert html.startswith("<!DOCTYPE html>")


def test_render_html_contains_entry_title(db):
    run, entries = _make_run_with_entries(db, n=1)
    html = render_digest_html(run, entries)
    assert "Story #1" in html


def test_render_html_contains_all_entries(db):
    run, entries = _make_run_with_entries(db, n=3)
    html = render_digest_html(run, entries)
    assert "Story #1" in html
    assert "Story #2" in html
    assert "Story #3" in html


def test_render_html_contains_digest_date(db):
    run = _make_run(db)
    html = render_digest_html(run, [])
    assert "2026-03-24" in html


def test_render_html_contains_section_name(db):
    run = _make_run(db)
    html = render_digest_html(run, [])
    assert "Companies Business" in html


def test_render_html_empty_run_no_crash(db):
    run = _make_run(db, total_included=0)
    html = render_digest_html(run, [])
    assert "No entries" in html
    assert "<!DOCTYPE html>" in html


def test_render_html_entry_contains_summaries(db):
    run, entries = _make_run_with_entries(db, n=1)
    html = render_digest_html(run, entries)
    assert "Summary EN #1" in html
    assert "Summary RU #1" in html


def test_render_html_entry_contains_why_it_matters(db):
    run, entries = _make_run_with_entries(db, n=1)
    html = render_digest_html(run, entries)
    assert "Why EN #1" in html
    assert "Why RU #1" in html


def test_render_html_entry_contains_score(db):
    run, entries = _make_run_with_entries(db, n=1)
    html = render_digest_html(run, entries)
    assert "0.900" in html


def test_render_html_escapes_special_chars(db):
    run = _make_run(db)
    entry = _make_entry(db, run, title="Acme & Co <b>raises</b> $50M")
    html = render_digest_html(run, [entry])
    assert "<b>" not in html
    assert "&amp;" in html
    assert "&lt;b&gt;" in html


def test_render_html_entries_in_rank_order(db):
    run, entries = _make_run_with_entries(db, n=3)
    html = render_digest_html(run, entries)
    pos1 = html.index("Story #1")
    pos2 = html.index("Story #2")
    pos3 = html.index("Story #3")
    assert pos1 < pos2 < pos3


# ── render_digest_page service ────────────────────────────────────────────────

def test_render_page_creates_digest_page(db):
    run, _ = _make_run_with_entries(db, n=2)
    page, created = render_digest_page(db, run)

    assert created is True
    assert page.digest_run_id == run.id
    assert page.slug == make_slug(run)
    assert page.html_content.startswith("<!DOCTYPE html>")
    assert page.rendered_at is not None


def test_render_page_persists_to_db(db):
    run, _ = _make_run_with_entries(db, n=1)
    page, _ = render_digest_page(db, run)

    fetched = db.get(DigestPage, page.id)
    assert fetched is not None
    assert fetched.slug == page.slug


def test_render_page_upserts_on_repeat(db):
    run, entries = _make_run_with_entries(db, n=1)

    page1, created1 = render_digest_page(db, run)
    page2, created2 = render_digest_page(db, run)

    assert created1 is True
    assert created2 is False
    # Same page row — stable ID
    assert page1.id == page2.id
    # Only one row in DB
    count = db.query(DigestPage).filter_by(digest_run_id=run.id).count()
    assert count == 1


def test_render_page_updates_html_on_repeat(db):
    run, entries = _make_run_with_entries(db, n=1)
    render_digest_page(db, run)

    # Add another entry and re-render
    _make_entry(db, run, rank=2, title="New Story Added")
    page2, _ = render_digest_page(db, run)

    assert "New Story Added" in page2.html_content


def test_render_page_sets_rendered_at(db):
    run, _ = _make_run_with_entries(db, n=1)
    before = datetime.now(timezone.utc)
    page, _ = render_digest_page(db, run)
    after = datetime.now(timezone.utc)

    assert page.rendered_at is not None
    assert before <= page.rendered_at <= after


# ── GET /digest-pages/ ────────────────────────────────────────────────────────

def test_list_digest_pages_empty(client):
    resp = client.get("/digest-pages/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_digest_pages_returns_pages(client, db):
    run, _ = _make_run_with_entries(db, n=1)
    render_digest_page(db, run)

    resp = client.get("/digest-pages/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["slug"] == make_slug(run)
    assert "html_content" not in data[0]


# ── GET /digest-pages/{slug} ──────────────────────────────────────────────────

def test_get_digest_page_by_slug_returns_html(client, db):
    run, entries = _make_run_with_entries(db, n=1)
    render_digest_page(db, run)
    slug = make_slug(run)

    resp = client.get(f"/digest-pages/{slug}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<!DOCTYPE html>" in resp.text
    assert "Story #1" in resp.text


def test_get_digest_page_by_slug_not_found(client):
    resp = client.get("/digest-pages/nonexistent-slug")
    assert resp.status_code == 404


# ── POST /admin/digests/{id}/render ──────────────────────────────────────────

def test_admin_render_endpoint(client, db):
    run, _ = _make_run_with_entries(db, n=2)

    resp = client.post(f"/admin/digests/{run.id}/render")
    assert resp.status_code == 200
    data = resp.json()
    assert data["digest_run_id"] == str(run.id)
    assert data["slug"] == make_slug(run)
    assert data["created"] is True
    assert "digest_page_id" in data
    assert data["rendered_at"] is not None


def test_admin_render_endpoint_not_found(client):
    resp = client.post(f"/admin/digests/{uuid.uuid4()}/render")
    assert resp.status_code == 404


def test_admin_render_idempotent(client, db):
    run, _ = _make_run_with_entries(db, n=1)

    r1 = client.post(f"/admin/digests/{run.id}/render")
    r2 = client.post(f"/admin/digests/{run.id}/render")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["created"] is True
    assert r2.json()["created"] is False
    # Same page ID across both calls
    assert r1.json()["digest_page_id"] == r2.json()["digest_page_id"]
