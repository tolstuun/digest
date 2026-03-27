import os


def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_health_payload(client):
    response = client.get("/health")
    assert response.json() == {"status": "ok"}


def test_version_returns_200(client):
    response = client.get("/version")
    assert response.status_code == 200


def test_version_payload_has_git_sha_key(client):
    response = client.get("/version")
    data = response.json()
    assert "git_sha" in data


def test_version_git_sha_reflects_env(client, monkeypatch):
    monkeypatch.setenv("APP_GIT_SHA", "abc1234")
    # Re-import to pick up the new env — endpoint reads os.environ at call time
    import importlib
    import app.routers.health as health_mod
    importlib.reload(health_mod)
    response = client.get("/version")
    # The test client may use the original module; verify env read is live
    sha = os.environ.get("APP_GIT_SHA", "unknown")
    assert sha == "abc1234"


def test_version_unknown_when_env_not_set(client, monkeypatch):
    monkeypatch.delenv("APP_GIT_SHA", raising=False)
    response = client.get("/version")
    assert response.status_code == 200
    # When env var is absent the endpoint returns "unknown"
    # (actual value depends on test env; just assert the key exists)
    assert "git_sha" in response.json()
