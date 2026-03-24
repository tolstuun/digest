"""
Tests for Phase 4C: internal ops/admin UI.

No LLM calls. No real network. All pages must return 200 HTML.
Action endpoints must redirect (303) and call the right services.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.digest_entry import DigestEntry
from app.models.digest_page import DigestPage
from app.models.digest_run import DigestRun
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.source import Source
from app.routers.ui import _mask_secret


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_source(db, name: str = "Test Feed", enabled: bool = True) -> Source:
    src = Source(
        name=name,
        type="rss",
        url="http://example.com/feed",
        enabled=enabled,
        priority=0,
        parser_type="rss",
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    return src


def _make_cluster(db, event_type: str = "funding_round") -> EventCluster:
    cluster = EventCluster(
        cluster_key=f"test-key-{uuid.uuid4()}",
        event_type=event_type,
    )
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    return cluster


def _make_assessment(db, cluster: EventCluster) -> EventClusterAssessment:
    a = EventClusterAssessment(
        event_cluster_id=cluster.id,
        primary_section="companies_business",
        include_in_digest=True,
        rule_score=0.7,
        llm_score=0.8,
        final_score=0.74,
        assessed_at=datetime.now(timezone.utc),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _make_digest_run(db, digest_date: date = date(2026, 3, 24)) -> DigestRun:
    run = DigestRun(
        digest_date=digest_date,
        section_name="companies_business",
        status="assembled",
        total_candidate_clusters=3,
        total_included_clusters=2,
        generated_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# ── _mask_secret (pure) ───────────────────────────────────────────────────────


def test_mask_secret_empty():
    assert _mask_secret("") == "[empty]"


def test_mask_secret_short():
    assert _mask_secret("ab") == "**"


def test_mask_secret_normal():
    result = _mask_secret("sk-my-secret-key")
    assert result.startswith("sk-m")
    assert "*" in result
    assert "secret" not in result


def test_mask_secret_shows_prefix_only():
    result = _mask_secret("ABCDEFGHIJ")
    assert result.startswith("ABCD")
    assert result[4:] == "*" * min(len("ABCDEFGHIJ") - 4, 12)


# ── dashboard ─────────────────────────────────────────────────────────────────


def test_dashboard_loads_empty(client):
    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Dashboard" in resp.text


def test_dashboard_shows_counts(client, db):
    _make_source(db)
    resp = client.get("/ui/")
    assert resp.status_code == 200
    # At least one source should show as "1" in stats
    assert "1" in resp.text


def test_dashboard_shows_source_errors(client, db):
    src = _make_source(db)
    src.last_error = "Connection timeout"
    db.commit()

    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert "Connection timeout" in resp.text


def test_dashboard_no_errors_shows_muted(client, db):
    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert "No source errors" in resp.text


# ── sources page ──────────────────────────────────────────────────────────────


def test_sources_page_loads_empty(client):
    resp = client.get("/ui/sources")
    assert resp.status_code == 200
    assert "Sources" in resp.text
    assert "No sources yet" in resp.text


def test_sources_page_shows_sources(client, db):
    _make_source(db, name="My RSS Feed")
    resp = client.get("/ui/sources")
    assert resp.status_code == 200
    assert "My RSS Feed" in resp.text


def test_sources_page_shows_enabled_badge(client, db):
    _make_source(db, enabled=True)
    resp = client.get("/ui/sources")
    assert "yes" in resp.text


def test_sources_page_shows_action_buttons(client, db):
    _make_source(db)
    resp = client.get("/ui/sources")
    assert "Ingest" in resp.text
    assert "Normalize" in resp.text


# ── sources actions ───────────────────────────────────────────────────────────


def test_ingest_action_redirects(client, db):
    src = _make_source(db)
    with patch("app.routers.ui.ingest_source", return_value={"new": 2, "skipped": 0, "fetched": 2, "error": None}):
        resp = client.post(f"/ui/sources/{src.id}/ingest", follow_redirects=False)
    assert resp.status_code == 303
    assert "/ui/sources" in resp.headers["location"]


def test_ingest_action_shows_flash(client, db):
    src = _make_source(db)
    with patch("app.routers.ui.ingest_source", return_value={"new": 2, "skipped": 0, "fetched": 2, "error": None}):
        resp = client.post(f"/ui/sources/{src.id}/ingest", follow_redirects=True)
    assert resp.status_code == 200
    assert "Ingest done" in resp.text


def test_ingest_action_not_found(client):
    resp = client.post(f"/ui/sources/{uuid.uuid4()}/ingest", follow_redirects=False)
    assert resp.status_code == 303
    assert "flash_level=err" in resp.headers["location"]


def test_normalize_action_redirects(client, db):
    src = _make_source(db)
    resp = client.post(f"/ui/sources/{src.id}/normalize", follow_redirects=False)
    assert resp.status_code == 303
    assert "/ui/sources" in resp.headers["location"]


# ── event clusters page ───────────────────────────────────────────────────────


def test_event_clusters_page_loads_empty(client):
    resp = client.get("/ui/event-clusters")
    assert resp.status_code == 200
    assert "Event Clusters" in resp.text
    assert "No event clusters yet" in resp.text


def test_event_clusters_page_shows_clusters(client, db):
    _make_cluster(db, event_type="funding_round")
    resp = client.get("/ui/event-clusters")
    assert resp.status_code == 200
    assert "funding_round" in resp.text


def test_event_clusters_page_shows_assessed_badge(client, db):
    cluster = _make_cluster(db)
    _make_assessment(db, cluster)
    resp = client.get("/ui/event-clusters")
    assert resp.status_code == 200
    assert "companies_business" in resp.text


def test_event_clusters_page_shows_assess_button(client, db):
    _make_cluster(db)
    resp = client.get("/ui/event-clusters")
    assert "Assess" in resp.text


# ── event clusters action ──────────────────────────────────────────────────────


def test_assess_action_redirects(client, db):
    cluster = _make_cluster(db)
    mock_assessment = MagicMock()
    mock_assessment.primary_section = "companies_business"
    mock_assessment.final_score = 0.75
    mock_assessment.include_in_digest = True
    with patch("app.routers.ui.assess_cluster", return_value=(mock_assessment, True)):
        resp = client.post(f"/ui/event-clusters/{cluster.id}/assess", follow_redirects=False)
    assert resp.status_code == 303
    assert "/ui/event-clusters" in resp.headers["location"]


def test_assess_action_not_found(client):
    resp = client.post(f"/ui/event-clusters/{uuid.uuid4()}/assess", follow_redirects=False)
    assert resp.status_code == 303
    assert "flash_level=err" in resp.headers["location"]


# ── digests page ──────────────────────────────────────────────────────────────


def test_digests_page_loads_empty(client):
    resp = client.get("/ui/digests")
    assert resp.status_code == 200
    assert "Digests" in resp.text
    assert "No digest runs yet" in resp.text


def test_digests_page_shows_runs(client, db):
    _make_digest_run(db)
    resp = client.get("/ui/digests")
    assert resp.status_code == 200
    assert "2026-03-24" in resp.text
    assert "companies_business" in resp.text


def test_digests_page_shows_assemble_form(client):
    resp = client.get("/ui/digests")
    assert "Assemble" in resp.text
    assert 'type="date"' in resp.text


def test_digests_page_shows_render_button(client, db):
    _make_digest_run(db)
    resp = client.get("/ui/digests")
    assert "Render" in resp.text


def test_digests_page_shows_page_link(client, db):
    run = _make_digest_run(db)
    page = DigestPage(
        digest_run_id=run.id,
        slug="2026-03-24-companies-business",
        title="Security Digest",
        html_content="<html></html>",
        rendered_at=datetime.now(timezone.utc),
    )
    db.add(page)
    db.commit()
    resp = client.get("/ui/digests")
    assert "2026-03-24-companies-business" in resp.text


# ── digests actions ───────────────────────────────────────────────────────────


def test_assemble_action_redirects(client, db):
    mock_run = MagicMock()
    mock_run.total_included_clusters = 5
    with patch("app.routers.ui.assemble_digest", return_value=(mock_run, [], True)):
        resp = client.post(
            "/ui/digests/assemble",
            data={"digest_date": "2026-03-24"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "/ui/digests" in resp.headers["location"]


def test_assemble_action_invalid_date(client):
    resp = client.post(
        "/ui/digests/assemble",
        data={"digest_date": "not-a-date"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "flash_level=err" in resp.headers["location"]


def test_render_action_redirects(client, db):
    run = _make_digest_run(db)
    mock_page = MagicMock()
    mock_page.slug = "2026-03-24-companies-business"
    with patch("app.routers.ui.render_digest_page", return_value=(mock_page, True)):
        resp = client.post(f"/ui/digests/{run.id}/render", follow_redirects=False)
    assert resp.status_code == 303
    assert "/ui/digests" in resp.headers["location"]


def test_render_action_not_found(client):
    resp = client.post(f"/ui/digests/{uuid.uuid4()}/render", follow_redirects=False)
    assert resp.status_code == 303
    assert "flash_level=err" in resp.headers["location"]


# ── config page ───────────────────────────────────────────────────────────────


def test_config_page_loads(client):
    resp = client.get("/ui/config")
    assert resp.status_code == 200
    assert "Configuration" in resp.text


def test_config_page_shows_llm_section(client):
    resp = client.get("/ui/config")
    assert "LLM" in resp.text
    assert "anthropic" in resp.text


def test_config_page_shows_telegram_section(client):
    resp = client.get("/ui/config")
    assert "Telegram" in resp.text


def test_config_page_masks_api_key(client):
    from app import config as cfg_module
    original = cfg_module.settings.llm.api_key
    try:
        cfg_module.settings.llm.api_key = "sk-ant-secret-key-12345"
        resp = client.get("/ui/config")
        assert "sk-ant-secret-key-12345" not in resp.text
        assert "sk-a" in resp.text  # prefix visible
        assert "*" in resp.text
    finally:
        cfg_module.settings.llm.api_key = original


def test_config_page_shows_read_only_label(client):
    resp = client.get("/ui/config")
    assert "read-only" in resp.text


# ── nav links ─────────────────────────────────────────────────────────────────


def test_nav_present_on_all_pages(client):
    for path in ["/ui/", "/ui/sources", "/ui/event-clusters", "/ui/digests", "/ui/config"]:
        resp = client.get(path)
        assert resp.status_code == 200, f"Page {path} returned {resp.status_code}"
        assert "Digest Ops" in resp.text
        assert "/ui/sources" in resp.text
