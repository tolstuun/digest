# Security Digest

A modular daily cybersecurity digest platform. Collects, enriches, and publishes structured digests covering business and company news in cybersecurity (funding, M&A, earnings, market moves). Additional sections (incidents, regulation, vendor launches, conferences) will follow.

Publishes a daily web page and sends a Telegram message linking to it.

---

## Current scope (Phase 1 — source registry + ingestion foundation)

**Phase 0 (bootstrap) — complete:**
- FastAPI application skeleton
- PostgreSQL integration
- `GET /health` — health check endpoint
- `sources` table — editable source registry
- `GET /sources/`, `POST /sources/` — source list and create

**Phase 1 (current) — complete:**
- `GET /sources/{id}` — fetch source by ID
- `PATCH /sources/{id}` — partial update (any field)
- Extended source model with ingestion management fields: `parser_type`, `poll_frequency_minutes`, `last_polled_at`, `last_success_at`, `last_error`, `section_scope`
- `raw_items` table — raw ingest store with deduplication
- RSS ingestion: fetch, parse, and persist raw items (feedparser-based, deterministic)
- `POST /admin/sources/{id}/ingest` — manually trigger one-source ingestion
- Idempotent ingestion: repeated runs skip already-stored items (by `content_hash`)
- Source state tracking: `last_polled_at`, `last_success_at`, `last_error` updated on each run

**Not yet implemented:** normalization, clustering, enrichment/scoring, digest assembly, publishing.

---

## Local development

### Prerequisites

- Docker and Docker Compose

### Start the stack

```bash
docker compose up --build
```

App: http://localhost:8000
API docs: http://localhost:8000/docs

### Run migrations

```bash
docker compose run --rm app alembic upgrade head
```

### Run tests

```bash
docker compose run --rm app pytest -v
```

Single test:

```bash
docker compose run --rm app pytest tests/test_ingestion.py::test_ingest_avoids_duplicates -v
```

### Without Docker (requires local PostgreSQL)

```bash
cp .env.example .env
# Edit DATABASE_URL in .env
pip install -r requirements-dev.txt
alembic upgrade head
pytest -v
uvicorn app.main:app --reload
```

---

## Running ingestion manually

To manually ingest one source (useful for dev and smoke-testing):

```bash
# Via the admin endpoint (app must be running)
curl -X POST http://localhost:8000/admin/sources/{source_id}/ingest

# Or via docker compose
docker compose run --rm app python -c "
import os
from sqlalchemy.orm import Session
from app.database import engine
from app.models.source import Source
from app.ingestion.service import ingest_source
import uuid

source_id = uuid.UUID('YOUR-SOURCE-UUID')
with Session(engine) as db:
    source = db.get(Source, source_id)
    result = ingest_source(db, source)
    print(result)
"
```

---

## API

| Method | Path                             | Description                          |
|--------|----------------------------------|--------------------------------------|
| GET    | /health                          | Health check                         |
| GET    | /sources/                        | List all sources                     |
| GET    | /sources/{id}                    | Get source by ID                     |
| POST   | /sources/                        | Create a source                      |
| PATCH  | /sources/{id}                    | Partial update a source              |
| POST   | /admin/sources/{id}/ingest       | Trigger ingestion for one source     |

Full interactive docs: http://localhost:8000/docs

### Source fields

| Field                   | Type                                                  | Required | Default |
|-------------------------|-------------------------------------------------------|----------|---------|
| name                    | string                                                | yes      |         |
| type                    | `rss` \| `api` \| `html` \| `manual` \| `newsletter` | yes      |         |
| url                     | string                                                | no       | null    |
| enabled                 | boolean                                               | no       | true    |
| tags                    | list[string]                                          | no       | null    |
| language                | string (e.g. `en`)                                    | no       | null    |
| geography               | string (e.g. `us`)                                    | no       | null    |
| priority                | integer                                               | no       | 0       |
| notes                   | string                                                | no       | null    |
| parser_type             | string (e.g. `feedparser`)                            | no       | null    |
| poll_frequency_minutes  | integer                                               | no       | null    |
| section_scope           | list[string]                                          | no       | null    |

Read-only fields (set by ingestion): `last_polled_at`, `last_success_at`, `last_error`.

---

## Project structure

```
app/
  config.py             settings (DATABASE_URL via env)
  database.py           SQLAlchemy engine, session, Base
  main.py               FastAPI app, router registration, startup logging
  models/
    source.py           Source ORM model
    raw_item.py         RawItem ORM model (raw ingest store)
  schemas/
    source.py           SourceCreate / SourcePatch / SourceOut
  routers/
    health.py           GET /health
    sources.py          GET /sources/, GET /sources/{id}, POST, PATCH
    admin.py            POST /admin/sources/{id}/ingest
  ingestion/
    rss.py              RSS feed fetching and parsing (feedparser, no DB)
    service.py          ingest_source() — orchestrates fetch + persist + state update
alembic/
  versions/
    0001_initial_sources.py
    0002_source_ingestion_fields.py
    0003_add_raw_items.py
tests/
  conftest.py           session-scoped DB setup, per-test truncation, TestClient
  test_health.py
  test_sources.py       list, create, get by id, patch, validation
  test_ingestion.py     RSS parsing, raw item persistence, dedup, endpoint
```

---

## See also

- [Roadmap](docs/roadmap.md)
- [Architecture](docs/architecture.md)
