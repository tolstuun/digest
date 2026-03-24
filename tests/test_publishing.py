"""
Tests for Phase 4D: Telegram publishing.

No real network calls. All Telegram HTTP is mocked.
"""
import uuid
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from app.config import Settings, AppConfig, DatabaseConfig, LLMConfig, TelegramConfig
from app.models.digest_entry import DigestEntry
from app.models.digest_page import DigestPage
from app.models.digest_publication import DigestPublication
from app.models.digest_run import DigestRun
from app.publishing.service import CHANNEL_TYPE, publish_to_telegram
from app.publishing.telegram import build_message_text
from app.rendering.service import render_digest_page


# ── helpers ───────────────────────────────────────────────────────────────────

TARGET_DATE = date(2026, 3, 24)
SECTION = "companies_business"


def _make_settings(enabled: bool = True) -> Settings:
    s = Settings(
        config_path="test",
        app=AppConfig(public_base_url="https://digest.example.com"),
        database=DatabaseConfig(),
        llm=LLMConfig(),
        telegram=TelegramConfig(
            enabled=enabled,
            bot_token="test-bot-token",
            chat_id="-1001234567890",
        ),
    )
    return s


def _make_run(db, digest_date: date = TARGET_DATE, section_name: str = SECTION) -> DigestRun:
    run = DigestRun(
        digest_date=digest_date,
        section_name=section_name,
        status="assembled",
        total_candidate_clusters=1,
        total_included_clusters=1,
        generated_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _make_entry(db, run: DigestRun) -> DigestEntry:
    entry = DigestEntry(
        digest_run_id=run.id,
        event_cluster_id=None,
        rank=1,
        final_score=0.8,
        title="Acme raises $50M",
        canonical_summary_en="Acme raised funding.",
        canonical_summary_ru="Компания привлекла финансирование.",
        why_it_matters_en="Important.",
        why_it_matters_ru="Важно.",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def _make_page(db, run: DigestRun) -> DigestPage:
    page, _ = render_digest_page(db, run)
    return page


# ── build_message_text (pure) ─────────────────────────────────────────────────


def test_build_message_text_contains_title():
    text = build_message_text(
        title="Security Digest — 2026-03-24 — Companies Business",
        digest_date=date(2026, 3, 24),
        section_name="companies_business",
        public_url="https://digest.example.com/digest-pages/2026-03-24-companies-business",
    )
    assert "Security Digest" in text


def test_build_message_text_contains_date():
    text = build_message_text(
        title="Digest title",
        digest_date=date(2026, 3, 24),
        section_name="companies_business",
        public_url="https://digest.example.com/digest-pages/2026-03-24-companies-business",
    )
    assert "2026-03-24" in text


def test_build_message_text_contains_section():
    text = build_message_text(
        title="Digest title",
        digest_date=date(2026, 3, 24),
        section_name="companies_business",
        public_url="https://digest.example.com/digest-pages/2026-03-24-companies-business",
    )
    assert "Companies Business" in text


def test_build_message_text_contains_public_url():
    url = "https://digest.example.com/digest-pages/2026-03-24-companies-business"
    text = build_message_text(
        title="Digest title",
        digest_date=date(2026, 3, 24),
        section_name="companies_business",
        public_url=url,
    )
    assert url in text


# ── publish_to_telegram service ───────────────────────────────────────────────


def test_publish_creates_publication_record(db):
    run = _make_run(db)
    _make_entry(db, run)
    page = _make_page(db, run)
    cfg = _make_settings(enabled=True)

    with patch("app.publishing.service.send_telegram_message", return_value="42") as mock_send:
        pub, created = publish_to_telegram(db, page, cfg)

    assert created is True
    assert pub is not None
    assert pub.digest_page_id == page.id
    assert pub.channel_type == CHANNEL_TYPE
    assert pub.target == cfg.telegram.chat_id
    assert pub.provider_message_id == "42"
    assert pub.status == "sent"
    assert pub.published_at is not None
    mock_send.assert_called_once()


def test_publish_persists_to_db(db):
    run = _make_run(db)
    _make_entry(db, run)
    page = _make_page(db, run)
    cfg = _make_settings()

    with patch("app.publishing.service.send_telegram_message", return_value="99"):
        pub, _ = publish_to_telegram(db, page, cfg)

    fetched = db.get(DigestPublication, pub.id)
    assert fetched is not None
    assert fetched.provider_message_id == "99"


def test_publish_idempotent_updates_existing(db):
    run = _make_run(db)
    _make_entry(db, run)
    page = _make_page(db, run)
    cfg = _make_settings()

    with patch("app.publishing.service.send_telegram_message", return_value="10"):
        pub1, created1 = publish_to_telegram(db, page, cfg)

    with patch("app.publishing.service.send_telegram_message", return_value="11"):
        pub2, created2 = publish_to_telegram(db, page, cfg)

    assert created1 is True
    assert created2 is False
    assert pub1.id == pub2.id  # same row
    assert pub2.provider_message_id == "11"

    count = db.query(DigestPublication).filter_by(digest_page_id=page.id).count()
    assert count == 1


def test_publish_calls_send_with_correct_args(db):
    run = _make_run(db)
    _make_entry(db, run)
    page = _make_page(db, run)
    cfg = _make_settings()

    with patch("app.publishing.service.send_telegram_message", return_value="55") as mock_send:
        publish_to_telegram(db, page, cfg)

    mock_send.assert_called_once_with(
        cfg.telegram.bot_token,
        cfg.telegram.chat_id,
        mock_send.call_args[0][2],  # message_text (third positional arg)
    )
    # Verify message contains public URL
    call_text = mock_send.call_args[0][2]
    assert "digest-pages" in call_text
    assert page.slug in call_text


def test_publish_raises_when_disabled(db):
    run = _make_run(db)
    _make_entry(db, run)
    page = _make_page(db, run)
    cfg = _make_settings(enabled=False)

    with pytest.raises(ValueError, match="not enabled"):
        publish_to_telegram(db, page, cfg)


def test_publish_raises_when_no_bot_token(db):
    run = _make_run(db)
    _make_entry(db, run)
    page = _make_page(db, run)
    cfg = _make_settings()
    cfg.telegram.bot_token = ""

    with pytest.raises(ValueError, match="bot_token"):
        publish_to_telegram(db, page, cfg)


def test_publish_message_text_stored_in_db(db):
    run = _make_run(db)
    _make_entry(db, run)
    page = _make_page(db, run)
    cfg = _make_settings()

    with patch("app.publishing.service.send_telegram_message", return_value="77"):
        pub, _ = publish_to_telegram(db, page, cfg)

    assert page.slug in pub.message_text
    assert cfg.app.public_base_url in pub.message_text


def test_publish_public_url_uses_base_url_and_slug(db):
    run = _make_run(db)
    _make_entry(db, run)
    page = _make_page(db, run)
    cfg = _make_settings()

    with patch("app.publishing.service.send_telegram_message", return_value="1") as mock_send:
        publish_to_telegram(db, page, cfg)

    call_text = mock_send.call_args[0][2]
    expected_url = f"{cfg.app.public_base_url}/digest-pages/{page.slug}"
    assert expected_url in call_text


# ── GET /digest-publications/ ─────────────────────────────────────────────────


def test_list_publications_empty(client):
    resp = client.get("/digest-publications/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_publications_returns_records(client, db):
    run = _make_run(db)
    _make_entry(db, run)
    page = _make_page(db, run)
    cfg = _make_settings()

    with patch("app.publishing.service.send_telegram_message", return_value="99"):
        from app.publishing.service import publish_to_telegram as _pub
        _pub(db, page, cfg)

    resp = client.get("/digest-publications/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["channel_type"] == "telegram"
    assert data[0]["status"] == "sent"


def test_get_publication_by_id(client, db):
    run = _make_run(db)
    _make_entry(db, run)
    page = _make_page(db, run)
    cfg = _make_settings()

    with patch("app.publishing.service.send_telegram_message", return_value="42"):
        from app.publishing.service import publish_to_telegram as _pub
        pub, _ = _pub(db, page, cfg)

    resp = client.get(f"/digest-publications/{pub.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(pub.id)
    assert data["provider_message_id"] == "42"


def test_get_publication_not_found(client):
    resp = client.get(f"/digest-publications/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── POST /admin/digest-pages/{id}/publish-telegram ───────────────────────────


def test_admin_publish_telegram_endpoint(client, db):
    run = _make_run(db)
    _make_entry(db, run)
    page = _make_page(db, run)

    with patch("app.routers.admin.settings") as mock_cfg:
        mock_cfg.telegram.enabled = True
        mock_cfg.telegram.bot_token = "tok"
        mock_cfg.telegram.chat_id = "-100123"
        mock_cfg.app.public_base_url = "https://example.com"
        with patch("app.publishing.service.send_telegram_message", return_value="777"):
            resp = client.post(f"/admin/digest-pages/{page.id}/publish-telegram")

    assert resp.status_code == 200
    data = resp.json()
    assert data["digest_page_id"] == str(page.id)
    assert data["channel_type"] == "telegram"
    assert data["status"] == "sent"


def test_admin_publish_telegram_not_found(client):
    resp = client.post(f"/admin/digest-pages/{uuid.uuid4()}/publish-telegram")
    assert resp.status_code == 404


def test_admin_publish_telegram_disabled_returns_400(client, db):
    run = _make_run(db)
    _make_entry(db, run)
    page = _make_page(db, run)

    with patch("app.routers.admin.settings") as mock_cfg:
        mock_cfg.telegram.enabled = False
        resp = client.post(f"/admin/digest-pages/{page.id}/publish-telegram")

    assert resp.status_code == 400
    assert "not enabled" in resp.json()["detail"]


# ── UI: POST /ui/digest-pages/{page_id}/publish-telegram ─────────────────────


def test_ui_publish_telegram_redirects(client, db):
    run = _make_run(db)
    _make_entry(db, run)
    page = _make_page(db, run)

    with patch("app.routers.ui.publish_to_telegram") as mock_pub:
        mock_pub.return_value = (
            DigestPublication(
                id=uuid.uuid4(),
                digest_page_id=page.id,
                channel_type="telegram",
                target="-100",
                message_text="msg",
                provider_message_id="5",
                status="sent",
                published_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            True,
        )
        resp = client.post(
            f"/ui/digest-pages/{page.id}/publish-telegram",
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "/ui/digests" in resp.headers["location"]


def test_ui_publish_telegram_page_not_found(client):
    resp = client.post(
        f"/ui/digest-pages/{uuid.uuid4()}/publish-telegram",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "err" in resp.headers["location"]


def test_ui_publish_telegram_failure_flash(client, db):
    run = _make_run(db)
    _make_entry(db, run)
    page = _make_page(db, run)

    with patch("app.routers.ui.publish_to_telegram", side_effect=Exception("TG error")):
        resp = client.post(
            f"/ui/digest-pages/{page.id}/publish-telegram",
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "err" in resp.headers["location"]
    assert "Publish+failed" in resp.headers["location"] or "Publish%20failed" in resp.headers["location"] or "Publish" in resp.headers["location"]
