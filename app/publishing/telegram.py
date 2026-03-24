"""
Telegram publishing boundary.

Narrow HTTP boundary for sending messages via the Telegram Bot API.
All functions in this module are mockable in tests.

Public interface:
  build_message_text(title, digest_date, section_name, public_url) -> str
  send_telegram_message(bot_token, chat_id, text) -> str  (returns message_id)
"""
from __future__ import annotations

import logging
from datetime import date

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org"


def build_message_text(
    title: str,
    digest_date: date,
    section_name: str,
    public_url: str,
) -> str:
    """Build plain-text Telegram message for a published digest page."""
    section_display = section_name.replace("_", " ").title()
    date_str = digest_date.isoformat() if hasattr(digest_date, "isoformat") else str(digest_date)
    return (
        f"{title}\n"
        f"Date: {date_str}\n"
        f"Section: {section_display}\n"
        f"{public_url}"
    )


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> str:
    """
    Send a text message via Telegram Bot API.

    Returns the Telegram message_id as a string.
    Raises httpx.HTTPStatusError on non-2xx responses.
    Raises httpx.RequestError on network errors.
    """
    url = f"{_TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False,
    }
    logger.info("Sending Telegram message to chat_id=%s", chat_id)
    response = httpx.post(url, json=payload, timeout=15)
    response.raise_for_status()
    data = response.json()
    message_id = str(data["result"]["message_id"])
    logger.info("Telegram message sent message_id=%s", message_id)
    return message_id
