"""
Tests for the YAML config loader (app.config).

No DB, no network, no LLM calls.
"""
import os
import tempfile
from pathlib import Path

import pytest

from app.config import (
    AppConfig,
    DatabaseConfig,
    LLMConfig,
    Settings,
    TelegramConfig,
    load_settings,
)


# ── defaults ──────────────────────────────────────────────────────────────────


def test_defaults_without_config_file():
    s = load_settings(config_path="/nonexistent/settings.yaml")
    assert s.database.url.startswith("postgresql://")
    assert s.llm.provider == "anthropic"
    assert s.telegram.enabled is False


def test_backward_compat_database_url():
    s = load_settings(config_path="/nonexistent/settings.yaml")
    assert s.database_url == s.database.url


def test_backward_compat_anthropic_api_key():
    s = load_settings(config_path="/nonexistent/settings.yaml")
    assert s.anthropic_api_key == s.llm.api_key


def test_backward_compat_extraction_model():
    s = load_settings(config_path="/nonexistent/settings.yaml")
    assert s.extraction_model == s.llm.model_extraction


# ── YAML loading ──────────────────────────────────────────────────────────────


def _write_yaml(content: str) -> str:
    """Write content to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(content)
    f.flush()
    f.close()
    return f.name


def test_yaml_overrides_database_url(monkeypatch):
    # Remove DATABASE_URL env var so YAML value is not overridden
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = _write_yaml("""
database:
  url: "postgresql://user:pass@myhost:5433/mydb"
""")
    try:
        s = load_settings(config_path=path)
        assert s.database.url == "postgresql://user:pass@myhost:5433/mydb"
    finally:
        os.unlink(path)


def test_yaml_overrides_llm_api_key():
    path = _write_yaml("""
llm:
  api_key: "sk-test-yaml-key"
  model_extraction: "claude-test-model"
  model_scoring: "claude-test-model"
""")
    try:
        s = load_settings(config_path=path)
        assert s.llm.api_key == "sk-test-yaml-key"
        assert s.llm.model_extraction == "claude-test-model"
        assert s.llm.model_scoring == "claude-test-model"
    finally:
        os.unlink(path)


def test_yaml_overrides_telegram():
    path = _write_yaml("""
telegram:
  enabled: true
  bot_token: "bot-token-123"
  chat_id: "-1001234567890"
""")
    try:
        s = load_settings(config_path=path)
        assert s.telegram.enabled is True
        assert s.telegram.bot_token == "bot-token-123"
        assert s.telegram.chat_id == "-1001234567890"
    finally:
        os.unlink(path)


def test_yaml_overrides_app_base_url():
    path = _write_yaml("""
app:
  public_base_url: "https://digest.example.com"
""")
    try:
        s = load_settings(config_path=path)
        assert s.app.public_base_url == "https://digest.example.com"
    finally:
        os.unlink(path)


def test_empty_yaml_uses_defaults():
    path = _write_yaml("")
    try:
        s = load_settings(config_path=path)
        assert s.database.url.startswith("postgresql://")
        assert s.telegram.enabled is False
    finally:
        os.unlink(path)


def test_partial_yaml_leaves_other_defaults():
    path = _write_yaml("""
app:
  public_base_url: "https://example.com"
""")
    try:
        s = load_settings(config_path=path)
        assert s.app.public_base_url == "https://example.com"
        # Other sections untouched
        assert s.telegram.enabled is False
        assert s.llm.provider == "anthropic"
    finally:
        os.unlink(path)


# ── env var overrides ─────────────────────────────────────────────────────────


def test_env_var_overrides_yaml_database_url(monkeypatch):
    path = _write_yaml("""
database:
  url: "postgresql://yaml:yaml@yaml-host/yamldb"
""")
    try:
        monkeypatch.setenv("DATABASE_URL", "postgresql://env:env@env-host/envdb")
        s = load_settings(config_path=path)
        assert s.database.url == "postgresql://env:env@env-host/envdb"
    finally:
        os.unlink(path)


def test_env_var_overrides_yaml_api_key(monkeypatch):
    path = _write_yaml("""
llm:
  api_key: "yaml-key"
""")
    try:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
        s = load_settings(config_path=path)
        assert s.llm.api_key == "env-key"
    finally:
        os.unlink(path)


# ── example file validity ─────────────────────────────────────────────────────


def test_example_config_is_parseable():
    """config/settings.example.yaml must be valid YAML and load without error."""
    example_path = Path(__file__).parent.parent / "config" / "settings.example.yaml"
    assert example_path.exists(), "config/settings.example.yaml must exist"
    s = load_settings(config_path=str(example_path))
    # Should complete without exception and return a Settings object
    assert isinstance(s, Settings)
    assert isinstance(s.database, DatabaseConfig)
    assert isinstance(s.llm, LLMConfig)
    assert isinstance(s.telegram, TelegramConfig)
