# Security Digest

A modular daily cybersecurity digest platform. Collects, enriches, and publishes structured digests covering business and company news in cybersecurity (funding, M&A, earnings, market moves). Additional sections (incidents, regulation, vendor launches, conferences) will follow.

Publishes a daily web page and sends a Telegram message linking to it.

---

## Current scope (Phase 0 — bootstrap)

- FastAPI application skeleton
- PostgreSQL integration
- `GET /health` — health check endpoint
- `sources` table — editable registry of content sources
- `GET /sources` — list all sources
- `POST /sources` — create a source
- DB migration setup (Alembic)
- Docker Compose for local development

Not yet implemented: ingestion pipeline, normalization, enrichment, digest assembly, publishing.

---

## Local development

### Prerequisites

- Docker and Docker Compose

### Start the stack

```bash
docker compose up --build
```

App is available at http://localhost:8000
API docs at http://localhost:8000/docs

### Run migrations

```bash
docker compose run --rm app alembic upgrade head
```

### Run tests

```bash
docker compose run --rm app pytest -v
```

To run a single test:

```bash
docker compose run --rm app pytest tests/test_sources.py::test_create_source_success -v
```

### Without Docker (requires local PostgreSQL)

```bash
cp .env.example .env
# edit .env with your local DB credentials
pip install -r requirements-dev.txt
alembic upgrade head
pytest -v
uvicorn app.main:app --reload
```

---

## API

| Method | Path       | Description        |
|--------|------------|--------------------|
| GET    | /health    | Health check       |
| GET    | /sources/  | List all sources   |
| POST   | /sources/  | Create a source    |

Full interactive docs: http://localhost:8000/docs

### Source fields

| Field       | Type                                          | Required | Default |
|-------------|-----------------------------------------------|----------|---------|
| name        | string                                        | yes      |         |
| type        | `rss` \| `api` \| `html` \| `manual` \| `newsletter` | yes |    |
| url         | string                                        | no       | null    |
| enabled     | boolean                                       | no       | true    |
| tags        | list[string]                                  | no       | null    |
| language    | string (e.g. `en`)                            | no       | null    |
| geography   | string (e.g. `us`)                            | no       | null    |
| priority    | integer                                       | no       | 0       |
| notes       | string                                        | no       | null    |

---

## Project structure

```
app/
  config.py         settings (DATABASE_URL via env)
  database.py       SQLAlchemy engine, session, Base
  main.py           FastAPI app, router registration, startup logging
  models/
    source.py       Source ORM model
  schemas/
    source.py       Pydantic request/response schemas
  routers/
    health.py       GET /health
    sources.py      GET /sources/, POST /sources/
alembic/
  env.py            migration runner (reads DATABASE_URL from env)
  versions/
    0001_initial_sources.py
tests/
  conftest.py       session-scoped DB setup, per-test truncation, TestClient
  test_health.py
  test_sources.py
```

---

## See also

- [Roadmap](docs/roadmap.md)
- [Architecture](docs/architecture.md)
