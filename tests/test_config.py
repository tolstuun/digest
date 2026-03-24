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


# ── helpers ───────────────────────────────────────────────────────────────────


def _write_yaml(content: str) -> str:
    """Write content to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(content)
    f.flush()
    f.close()
    return f.name


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


def test_yaml_loads_database_url():
    path = _write_yaml("""
database:
  url: "postgresql://user:pass@myhost:5433/mydb"
""")
    try:
        s = load_settings(config_path=path)
        assert s.database.url == "postgresql://user:pass@myhost:5433/mydb"
    finally:
        os.unlink(path)


def test_yaml_loads_llm_api_key():
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


def test_yaml_loads_telegram():
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


def test_yaml_loads_app_base_url():
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
        # Other sections use built-in defaults
        assert s.telegram.enabled is False
        assert s.llm.provider == "anthropic"
    finally:
        os.unlink(path)


# ── env vars must NOT override runtime config ─────────────────────────────────
#
# These tests explicitly verify the YAML-only policy: runtime configuration
# values may not be set via environment variables. Only APP_CONFIG_PATH is
# allowed, and it only selects which file to load.


def test_database_url_env_var_does_not_override_yaml(monkeypatch):
    """DATABASE_URL env var must have no effect on loaded database.url."""
    path = _write_yaml("""
database:
  url: "postgresql://yaml:yaml@yaml-host/yamldb"
""")
    try:
        monkeypatch.setenv("DATABASE_URL", "postgresql://env:env@env-host/envdb")
        s = load_settings(config_path=path)
        assert s.database.url == "postgresql://yaml:yaml@yaml-host/yamldb"
    finally:
        os.unlink(path)


def test_anthropic_api_key_env_var_does_not_override_yaml(monkeypatch):
    """ANTHROPIC_API_KEY env var must have no effect on loaded llm.api_key."""
    path = _write_yaml("""
llm:
  api_key: "yaml-api-key"
""")
    try:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-api-key")
        s = load_settings(config_path=path)
        assert s.llm.api_key == "yaml-api-key"
    finally:
        os.unlink(path)


def test_database_url_env_var_does_not_override_defaults(monkeypatch):
    """DATABASE_URL env var must have no effect even when no YAML file exists."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://env:env@env-host/envdb")
    s = load_settings(config_path="/nonexistent/settings.yaml")
    assert s.database.url != "postgresql://env:env@env-host/envdb"
    assert s.database.url.startswith("postgresql://")  # still the built-in default


def test_extraction_model_env_var_does_not_override_yaml(monkeypatch):
    """EXTRACTION_MODEL env var must have no effect on loaded config."""
    path = _write_yaml("""
llm:
  model_extraction: "yaml-extraction-model"
""")
    try:
        monkeypatch.setenv("EXTRACTION_MODEL", "env-extraction-model")
        s = load_settings(config_path=path)
        assert s.llm.model_extraction == "yaml-extraction-model"
    finally:
        os.unlink(path)


# ── APP_CONFIG_PATH selects file only ─────────────────────────────────────────


def test_app_config_path_selects_which_file_is_loaded(monkeypatch):
    """APP_CONFIG_PATH env var selects which file to load, nothing else."""
    path = _write_yaml("""
app:
  public_base_url: "https://selected-by-env.example.com"
""")
    try:
        monkeypatch.setenv("APP_CONFIG_PATH", path)
        s = load_settings()  # no explicit path — uses APP_CONFIG_PATH
        assert s.app.public_base_url == "https://selected-by-env.example.com"
    finally:
        os.unlink(path)


def test_explicit_config_path_arg_takes_precedence_over_app_config_path(monkeypatch):
    """Explicit config_path argument takes precedence over APP_CONFIG_PATH."""
    env_path = _write_yaml("""
app:
  public_base_url: "https://from-env-path.example.com"
""")
    arg_path = _write_yaml("""
app:
  public_base_url: "https://from-arg-path.example.com"
""")
    try:
        monkeypatch.setenv("APP_CONFIG_PATH", env_path)
        s = load_settings(config_path=arg_path)
        assert s.app.public_base_url == "https://from-arg-path.example.com"
    finally:
        os.unlink(env_path)
        os.unlink(arg_path)


def test_config_path_is_recorded_in_settings():
    """Settings.config_path records the resolved path for observability (config UI)."""
    path = _write_yaml("app:\n  public_base_url: 'http://test'\n")
    try:
        s = load_settings(config_path=path)
        assert s.config_path == path
    finally:
        os.unlink(path)


# ── example file validity ─────────────────────────────────────────────────────


def test_example_config_is_parseable():
    """config/settings.example.yaml must be valid YAML and load without error."""
    example_path = Path(__file__).parent.parent / "config" / "settings.example.yaml"
    assert example_path.exists(), "config/settings.example.yaml must exist"
    s = load_settings(config_path=str(example_path))
    assert isinstance(s, Settings)
    assert isinstance(s.database, DatabaseConfig)
    assert isinstance(s.llm, LLMConfig)
    assert isinstance(s.telegram, TelegramConfig)


def test_compose_config_is_parseable():
    """config/settings.compose.yaml must be valid YAML and load without error."""
    compose_path = Path(__file__).parent.parent / "config" / "settings.compose.yaml"
    assert compose_path.exists(), "config/settings.compose.yaml must exist"
    s = load_settings(config_path=str(compose_path))
    assert isinstance(s, Settings)
    # Compose config must have the docker-compose db service hostname
    assert "db" in s.database.url
