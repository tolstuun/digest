"""
Tests: incidents section has full pipeline parity with companies_business.

Covers:
  - _run_write_digest iterates all sections (companies_business + incidents)
  - _run_render_digest creates a DigestPage for each section
  - _run_publish_telegram publishes each section's page
  - empty incidents digest still gets a rendered page (same as companies_business)
  - full end-to-end pipeline produces per-section results for write/render/publish
  - /ui/digests shows View and Publish TG for incidents rows that have a page
"""
import hashlib
import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.config import (
    AppConfig,
    DatabaseConfig,
    DigestConfig,
    LLMConfig,
    SchedulerConfig,
    Settings,
    TelegramConfig,
)
from app.digest.service import INCIDENTS_SECTION, SECTION_NAME, assemble_digest
from app.llm_usage.schemas import LlmUsageInfo
from app.models.digest_page import DigestPage
from app.models.digest_publication import DigestPublication
from app.models.digest_run import DigestRun
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts
from app.orchestration.service import (
    _run_publish_telegram,
    _run_render_digest,
    _run_write_digest,
    run_daily_pipeline,
)
from app.rendering.service import render_digest_page

TARGET_DATE = date(2026, 4, 1)

_counter = 0


def _unique() -> int:
    global _counter
    _counter += 1
    return _counter


def _dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc)


def _make_settings(telegram_enabled: bool = True) -> Settings:
    return Settings(
        config_path="test",
        app=AppConfig(public_base_url="https://example.com"),
        database=DatabaseConfig(),
        llm=LLMConfig(api_key="test-key"),
        telegram=TelegramConfig(
            enabled=telegram_enabled,
            bot_token="tok",
            chat_id="-100",
        ),
        scheduler=SchedulerConfig(enabled=False, publish_telegram_by_default=False),
        digest=DigestConfig(output_language="en"),
    )


def _mock_usage() -> LlmUsageInfo:
    return LlmUsageInfo(model_name="claude-haiku-4-5-20251001", input_tokens=50, output_tokens=20)


def _make_digest_run(db, section: str, status: str = "assembled") -> DigestRun:
    """Create a minimal DigestRun directly (bypassing assemble_digest)."""
    run = DigestRun(
        digest_date=TARGET_DATE,
        section_name=section,
        status=status,
        total_candidate_clusters=0,
        total_included_clusters=0,
        generated_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# ── _run_render_digest covers both sections ────────────────────────────────────


def test_render_digest_produces_page_for_both_sections(db):
    """_run_render_digest creates a DigestPage for companies_business AND incidents."""
    _make_digest_run(db, SECTION_NAME)
    _make_digest_run(db, INCIDENTS_SECTION)

    result = _run_render_digest(db, TARGET_DATE)

    sections = {s["section"]: s for s in result["sections"]}
    assert SECTION_NAME in sections
    assert INCIDENTS_SECTION in sections
    assert "digest_page_id" in sections[SECTION_NAME]
    assert "digest_page_id" in sections[INCIDENTS_SECTION]
    assert "slug" in sections[INCIDENTS_SECTION]


def test_render_digest_skips_with_reason_when_no_run(db):
    """If a section has no DigestRun, its entry is skipped with a reason."""
    # Only create companies_business run — no incidents run
    _make_digest_run(db, SECTION_NAME)

    result = _run_render_digest(db, TARGET_DATE)

    sections = {s["section"]: s for s in result["sections"]}
    assert sections[SECTION_NAME].get("skipped") is not True
    assert sections[INCIDENTS_SECTION].get("skipped") is True
    assert "reason" in sections[INCIDENTS_SECTION]


def test_render_digest_empty_incidents_gets_page(db):
    """An empty (0-entry) incidents digest still gets a rendered page."""
    run = _make_digest_run(db, INCIDENTS_SECTION, status="empty")

    page, created = render_digest_page(db, run)

    assert created is True
    assert page.id is not None
    assert page.digest_run_id == run.id


def test_render_digest_top_level_page_id_is_primary_section(db):
    """digest_page_id at top level points to companies_business page."""
    _make_digest_run(db, SECTION_NAME)
    _make_digest_run(db, INCIDENTS_SECTION)

    result = _run_render_digest(db, TARGET_DATE)

    assert "digest_page_id" in result
    cb_page_id = next(
        s["digest_page_id"]
        for s in result["sections"]
        if s["section"] == SECTION_NAME and "digest_page_id" in s
    )
    assert result["digest_page_id"] == cb_page_id


# ── _run_write_digest covers both sections ─────────────────────────────────────


def test_write_digest_returns_per_section_results(db):
    """_run_write_digest returns a sections list with one entry per section."""
    _make_digest_run(db, SECTION_NAME)
    _make_digest_run(db, INCIDENTS_SECTION)
    cfg = _make_settings()

    with patch("app.digest_writer.service.write_digest_entry_llm"):
        result = _run_write_digest(db, TARGET_DATE, cfg)

    sections = {s["section"]: s for s in result["sections"]}
    assert SECTION_NAME in sections
    assert INCIDENTS_SECTION in sections


def test_write_digest_skips_missing_section_with_reason(db):
    """If a section's DigestRun is missing, its entry is skipped with a reason."""
    _make_digest_run(db, SECTION_NAME)
    cfg = _make_settings()

    with patch("app.digest_writer.service.write_digest_entry_llm"):
        result = _run_write_digest(db, TARGET_DATE, cfg)

    sections = {s["section"]: s for s in result["sections"]}
    assert sections[INCIDENTS_SECTION].get("skipped") is True
    assert "reason" in sections[INCIDENTS_SECTION]


# ── _run_publish_telegram covers both sections ─────────────────────────────────


def test_publish_telegram_publishes_both_sections(db):
    """_run_publish_telegram publishes a page for each section."""
    cb_run = _make_digest_run(db, SECTION_NAME)
    inc_run = _make_digest_run(db, INCIDENTS_SECTION)
    render_digest_page(db, cb_run)
    render_digest_page(db, inc_run)
    cfg = _make_settings(telegram_enabled=True)

    with patch("app.publishing.service.send_telegram_message", return_value="99"):
        result = _run_publish_telegram(db, TARGET_DATE, cfg)

    sections = {s["section"]: s for s in result["sections"]}
    assert SECTION_NAME in sections
    assert INCIDENTS_SECTION in sections
    assert "digest_publication_id" in sections[SECTION_NAME]
    assert "digest_publication_id" in sections[INCIDENTS_SECTION]


def test_publish_telegram_skips_section_without_page(db):
    """If a section has no rendered page, its entry is skipped with a reason."""
    cb_run = _make_digest_run(db, SECTION_NAME)
    _make_digest_run(db, INCIDENTS_SECTION)
    render_digest_page(db, cb_run)  # only render companies_business
    cfg = _make_settings(telegram_enabled=True)

    with patch("app.publishing.service.send_telegram_message", return_value="99"):
        result = _run_publish_telegram(db, TARGET_DATE, cfg)

    sections = {s["section"]: s for s in result["sections"]}
    assert sections[INCIDENTS_SECTION].get("skipped") is True
    assert "reason" in sections[INCIDENTS_SECTION]


def test_publish_telegram_skips_all_when_disabled(db):
    """When telegram is disabled the whole step returns skipped immediately."""
    cfg = _make_settings(telegram_enabled=False)
    result = _run_publish_telegram(db, TARGET_DATE, cfg)
    assert result.get("skipped") is True


def test_publish_telegram_top_level_pub_id_is_primary_section(db):
    """digest_publication_id at top level belongs to companies_business."""
    cb_run = _make_digest_run(db, SECTION_NAME)
    inc_run = _make_digest_run(db, INCIDENTS_SECTION)
    render_digest_page(db, cb_run)
    render_digest_page(db, inc_run)
    cfg = _make_settings(telegram_enabled=True)

    with patch("app.publishing.service.send_telegram_message", return_value="77"):
        result = _run_publish_telegram(db, TARGET_DATE, cfg)

    assert "digest_publication_id" in result
    cb_pub_id = next(
        s["digest_publication_id"]
        for s in result["sections"]
        if s["section"] == SECTION_NAME and "digest_publication_id" in s
    )
    assert result["digest_publication_id"] == cb_pub_id


# ── full pipeline end-to-end: incidents gets page + publish ────────────────────


def _run_pipeline_mocked(db, publish_telegram: bool = True):
    cfg = _make_settings(telegram_enabled=True)
    with (
        patch("app.orchestration.service.ingest_source", return_value={"new": 0, "skipped": 0, "error": None}),
        patch("app.extraction.service.extract_facts_llm"),
        patch("app.scoring.llm.assess_cluster_llm"),
        patch("app.digest_writer.service.write_digest_entry_llm"),
        patch("app.publishing.service.send_telegram_message", return_value="42"),
    ):
        return run_daily_pipeline(
            db=db,
            run_date=TARGET_DATE,
            trigger_type="manual",
            publish_telegram=publish_telegram,
            cfg=cfg,
        )


def test_pipeline_creates_digest_pages_for_both_sections(db):
    """Full pipeline creates a DigestPage for both companies_business and incidents."""
    _run_pipeline_mocked(db)

    pages = db.query(DigestPage).all()
    section_names = {
        db.get(DigestRun, p.digest_run_id).section_name
        for p in pages
        if db.get(DigestRun, p.digest_run_id) is not None
    }
    assert SECTION_NAME in section_names
    assert INCIDENTS_SECTION in section_names


def test_pipeline_render_step_result_has_both_sections(db):
    """render_digest step_results.sections includes both section entries."""
    summary = _run_pipeline_mocked(db)

    render_result = summary["step_results"].get("render_digest", {})
    assert "sections" in render_result
    section_names = [s["section"] for s in render_result["sections"]]
    assert SECTION_NAME in section_names
    assert INCIDENTS_SECTION in section_names


def test_pipeline_write_step_result_has_both_sections(db):
    """write_digest step_results.sections includes both section entries."""
    summary = _run_pipeline_mocked(db)

    write_result = summary["step_results"].get("write_digest", {})
    assert "sections" in write_result
    section_names = [s["section"] for s in write_result["sections"]]
    assert SECTION_NAME in section_names
    assert INCIDENTS_SECTION in section_names


def test_pipeline_publish_step_result_has_both_sections(db):
    """publish_telegram step_results.sections includes both section entries."""
    summary = _run_pipeline_mocked(db, publish_telegram=True)

    pub_result = summary["step_results"].get("publish_telegram", {})
    assert "sections" in pub_result
    section_names = [s["section"] for s in pub_result["sections"]]
    assert SECTION_NAME in section_names
    assert INCIDENTS_SECTION in section_names


# ── /ui/digests shows View and Publish TG for incidents ───────────────────────


def test_ui_digests_shows_view_link_for_incidents(client, db):
    """The View link appears for an incidents digest run that has a rendered page."""
    run = _make_digest_run(db, INCIDENTS_SECTION)
    page, _ = render_digest_page(db, run)

    resp = client.get("/ui/digests")
    assert resp.status_code == 200
    # The slug should appear as a View link
    assert page.slug.encode() in resp.content


def test_ui_digests_shows_publish_tg_for_incidents(client, db):
    """Publish TG button appears for an incidents run that has a rendered page."""
    run = _make_digest_run(db, INCIDENTS_SECTION)
    page, _ = render_digest_page(db, run)

    # Patch settings so telegram_enabled=True in the UI route
    mock_settings = _make_settings(telegram_enabled=True)
    with patch("app.routers.ui.settings", mock_settings):
        resp = client.get("/ui/digests")

    assert resp.status_code == 200
    # Page ID appears in the form action for Publish TG
    assert str(page.id).encode() in resp.content
