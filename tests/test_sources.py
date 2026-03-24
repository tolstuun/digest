import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_source(client, **overrides):
    payload = {
        "name": "Krebs on Security",
        "type": "rss",
        "url": "https://krebsonsecurity.com/feed/",
        "enabled": True,
        "language": "en",
        "priority": 10,
        **overrides,
    }
    return client.post("/sources/", json=payload)


# ── list ─────────────────────────────────────────────────────────────────────

def test_list_sources_empty(client):
    response = client.get("/sources/")
    assert response.status_code == 200
    assert response.json() == []


def test_list_sources_returns_created(client):
    _make_source(client)
    response = client.get("/sources/")
    assert response.status_code == 200
    sources = response.json()
    assert len(sources) == 1
    assert sources[0]["name"] == "Krebs on Security"


# ── create ────────────────────────────────────────────────────────────────────

def test_create_source_success(client):
    response = _make_source(client)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Krebs on Security"
    assert data["type"] == "rss"
    assert data["enabled"] is True
    assert data["language"] == "en"
    assert data["priority"] == 10
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


def test_create_source_minimal_fields(client):
    """Only name and type are required."""
    response = client.post("/sources/", json={"name": "Minimal", "type": "manual"})
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Minimal"
    assert data["type"] == "manual"
    assert data["enabled"] is True   # default
    assert data["priority"] == 0     # default


def test_create_source_all_valid_types(client):
    for source_type in ("rss", "api", "html", "manual", "newsletter"):
        resp = client.post(
            "/sources/", json={"name": f"Source {source_type}", "type": source_type}
        )
        assert resp.status_code == 201, f"failed for type={source_type}"


# ── validation ────────────────────────────────────────────────────────────────

def test_create_source_invalid_type(client):
    response = _make_source(client, type="scraper")
    assert response.status_code == 422


def test_create_source_missing_name(client):
    response = client.post("/sources/", json={"type": "rss"})
    assert response.status_code == 422


def test_create_source_missing_type(client):
    response = client.post("/sources/", json={"name": "No type"})
    assert response.status_code == 422
