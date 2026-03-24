"""
Runtime configuration loader — YAML only.

All runtime configuration is read exclusively from a YAML file.
No runtime values (database URL, API keys, etc.) are read from environment
variables. The only environment variable recognised by this module is
APP_CONFIG_PATH, and it may only select which file to load.

Config file location (in order of precedence):
  1. config_path argument passed to load_settings()
  2. APP_CONFIG_PATH environment variable
  3. Default: config/settings.yaml

If the selected file does not exist, built-in defaults are used.

For Docker Compose development:
  - APP_CONFIG_PATH is set to /app/config/settings.compose.yaml in
    docker-compose.yml. That file is committed to the repo.

For server deployment:
  - Mount config/settings.yaml and set APP_CONFIG_PATH if needed.
  - Copy config/settings.example.yaml as a template.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = "config/settings.yaml"


# ── sub-sections ──────────────────────────────────────────────────────────────


@dataclass
class AppConfig:
    public_base_url: str = "http://localhost:8000"


@dataclass
class DatabaseConfig:
    url: str = "postgresql://digest:digest@localhost:5432/digest"


@dataclass
class LLMConfig:
    provider: str = "anthropic"
    api_key: str = ""
    model_extraction: str = "claude-haiku-4-5-20251001"
    model_scoring: str = "claude-haiku-4-5-20251001"


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


# ── top-level settings ────────────────────────────────────────────────────────


@dataclass
class Settings:
    config_path: str = _DEFAULT_CONFIG_PATH
    app: AppConfig = field(default_factory=AppConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)

    # ── backward-compat properties ────────────────────────────────────────────
    # Existing code that imports `settings.database_url` etc. keeps working.

    @property
    def database_url(self) -> str:
        return self.database.url

    @property
    def anthropic_api_key(self) -> str:
        return self.llm.api_key

    @property
    def extraction_model(self) -> str:
        return self.llm.model_extraction


# ── loader ────────────────────────────────────────────────────────────────────


def _load_yaml(path: str) -> dict:
    """Load YAML file; return empty dict on missing file or parse error."""
    try:
        import yaml  # lazy import
    except ImportError:
        logger.warning("pyyaml not installed; YAML config loading disabled")
        return {}

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        return data or {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to parse config file %s: %s", path, exc)
        return {}


def load_settings(config_path: Optional[str] = None) -> Settings:
    """
    Build a Settings object from a YAML file.

    Config file is selected (in order) by:
      1. config_path argument
      2. APP_CONFIG_PATH environment variable
      3. Default path config/settings.yaml

    APP_CONFIG_PATH is the only environment variable read here.
    No runtime configuration values are read from environment variables.
    """
    resolved_path = config_path or os.environ.get("APP_CONFIG_PATH", _DEFAULT_CONFIG_PATH)
    s = Settings(config_path=resolved_path)

    data = _load_yaml(resolved_path)
    if data:
        logger.info("Loaded config from %s", resolved_path)

    if "app" in data:
        app_data = data["app"]
        if "public_base_url" in app_data:
            s.app.public_base_url = app_data["public_base_url"]

    if "database" in data:
        db_data = data["database"]
        if "url" in db_data:
            s.database.url = db_data["url"]

    if "llm" in data:
        llm_data = data["llm"]
        if "provider" in llm_data:
            s.llm.provider = llm_data["provider"]
        if "api_key" in llm_data:
            s.llm.api_key = llm_data["api_key"]
        if "model_extraction" in llm_data:
            s.llm.model_extraction = llm_data["model_extraction"]
        if "model_scoring" in llm_data:
            s.llm.model_scoring = llm_data["model_scoring"]

    if "telegram" in data:
        tg_data = data["telegram"]
        if "enabled" in tg_data:
            s.telegram.enabled = bool(tg_data["enabled"])
        if "bot_token" in tg_data:
            s.telegram.bot_token = tg_data["bot_token"]
        if "chat_id" in tg_data:
            s.telegram.chat_id = str(tg_data["chat_id"])

    return s


# Module-level singleton — used by `from app.config import settings`
settings = load_settings()
