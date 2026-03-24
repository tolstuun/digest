"""
Telegram publishing service.

Idempotent: one row per (digest_page_id, channel_type, target).
Repeated calls update the existing record and re-send.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Tuple

from sqlalchemy.orm import Session

from app.config import Settings
from app.models.digest_page import DigestPage
from app.models.digest_publication import DigestPublication
from app.models.digest_run import DigestRun
from app.publishing.telegram import build_message_text, send_telegram_message

logger = logging.getLogger(__name__)

CHANNEL_TYPE = "telegram"


def publish_to_telegram(
    db: Session,
    page: DigestPage,
    cfg: Settings,
) -> Tuple[DigestPublication, bool]:
    """
    Publish a rendered digest page to Telegram.

    Idempotent: looks up existing publication record by
    (digest_page_id, channel_type, target).  If found, re-sends and updates.
    If not found, creates a new record.

    Returns (publication, created).
    Raises ValueError if Telegram is not enabled in config.
    Raises httpx errors on network/API failures.
    """
    if not cfg.telegram.enabled:
        raise ValueError("Telegram publishing is not enabled in config (telegram.enabled=false)")

    bot_token = cfg.telegram.bot_token
    chat_id = cfg.telegram.chat_id

    if not bot_token or not chat_id:
        raise ValueError("telegram.bot_token and telegram.chat_id must be set in config")

    # Build the public URL for this page
    public_url = f"{cfg.app.public_base_url}/digest-pages/{page.slug}"

    # Load the associated digest run to get date and section name
    run = db.get(DigestRun, page.digest_run_id)
    if run is None:
        raise ValueError(f"DigestRun not found for page {page.id}")

    message_text = build_message_text(
        title=page.title,
        digest_date=run.digest_date,
        section_name=run.section_name,
        public_url=public_url,
    )

    # Check for existing publication record
    existing = (
        db.query(DigestPublication)
        .filter_by(digest_page_id=page.id, channel_type=CHANNEL_TYPE, target=chat_id)
        .first()
    )
    created = existing is None

    # Send the message (may raise on network/API error)
    provider_message_id = send_telegram_message(bot_token, chat_id, message_text)

    now = datetime.now(timezone.utc)

    if existing is not None:
        existing.message_text = message_text
        existing.provider_message_id = provider_message_id
        existing.status = "sent"
        existing.published_at = now
        existing.updated_at = now
        pub = existing
    else:
        pub = DigestPublication(
            digest_page_id=page.id,
            channel_type=CHANNEL_TYPE,
            target=chat_id,
            message_text=message_text,
            provider_message_id=provider_message_id,
            status="sent",
            published_at=now,
        )
        db.add(pub)

    db.commit()
    db.refresh(pub)

    logger.info(
        "publish_to_telegram page=%s chat=%s message_id=%s created=%s",
        page.id, chat_id, provider_message_id, created,
    )
    return pub, created
