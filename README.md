# Security Digest

A modular daily cybersecurity digest platform. Collects, enriches, and publishes structured digests covering business and company news in cybersecurity (funding, M&A, earnings, market moves). Additional sections (incidents, regulation, vendor launches, conferences) will follow.

Publishes a daily web page and sends a Telegram message linking to it.

---

## Current scope

**Phase 0 — bootstrap** ✅
- FastAPI skeleton, PostgreSQL integration, health endpoint
- `sources` table, `GET /sources/`, `POST /sources/`

**Phase 1 — source registry + ingestion foundation** ✅
- Extended source model (ingestion management fields)
- `GET /sources/{id}`, `PATCH /sources/{id}`
- `raw_items` table, RSS ingestion, `POST /admin/sources/{id}/ingest`

**Phase 2A — normalization foundation** ✅ *(current)*
- `stories` table — one story per raw item
- Deterministic URL canonicalization (lowercase host/scheme, strip UTM/fbclid/gclid, remove fragment)
- `normalize_raw_item()` service — idempotent, no LLM
- `GET /stories/`, `GET /stories/{id}`
- `POST /admin/sources/{id}/normalize` — normalize all raw items for a source

**Not yet implemented:** clustering/deduplication across stories, LLM enrichment, sections, scoring, digest assembly, publishing.

---

## Local development

### Prerequisites

Docker and Docker Compose.

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
docker compose run --rm app pytest tests/test_normalization.py::test_normalize_idempotent_no_duplicate_in_db -v
```

### Without Docker (requires local PostgreSQL)

```bash
cp .env.example .env   # set DATABASE_URL
pip install -r requirements-dev.txt
alembic upgrade head
pytest -v
uvicorn app.main:app --reload
```

---

## Pipeline walkthrough (current)

```
1. Create a source via POST /sources/
2. Ingest it:   POST /admin/sources/{id}/ingest
3. Normalize:   POST /admin/sources/{id}/normalize
4. Read stories: GET /stories/
```

### Triggering normalization manually

```bash
# Normalize all raw items for a source
curl -X POST http://localhost:8000/admin/sources/{source_id}/normalize

# Check stories
curl http://localhost:8000/stories/
```

---

## API reference

| Method | Path                               | Description                              |
|--------|------------------------------------|------------------------------------------|
| GET    | /health                            | Health check                             |
| GET    | /sources/                          | List all sources                         |
| GET    | /sources/{id}                      | Get source by ID                         |
| POST   | /sources/                          | Create a source                          |
| PATCH  | /sources/{id}                      | Partial update a source                  |
| GET    | /stories/                          | List all stories                         |
| GET    | /stories/{id}                      | Get story by ID                          |
| POST   | /admin/sources/{id}/ingest         | Trigger RSS ingestion for one source     |
| POST   | /admin/sources/{id}/normalize      | Normalize all raw items for one source   |

Full interactive docs: http://localhost:8000/docs

---

## Project structure

```
app/
  config.py               settings (DATABASE_URL via env)
  database.py             SQLAlchemy engine, session, Base
  main.py                 FastAPI app, router registration
  models/
    source.py             Source ORM model
    raw_item.py           RawItem ORM model
    story.py              Story ORM model
  schemas/
    source.py             SourceCreate / SourcePatch / SourceOut
    story.py              StoryOut
  routers/
    health.py             GET /health
    sources.py            GET|POST /sources/, GET|PATCH /sources/{id}
    stories.py            GET /stories/, GET /stories/{id}
    admin.py              POST /admin/sources/{id}/ingest|normalize
  ingestion/
    rss.py                RSS feed fetch + parse (feedparser, no DB)
    service.py            ingest_source() — fetch → persist raw items
  normalization/
    urls.py               canonicalize_url() — deterministic, no DB, no LLM
    service.py            normalize_raw_item() — raw_item → story, idempotent
alembic/
  versions/
    0001_initial_sources.py
    0002_source_ingestion_fields.py
    0003_add_raw_items.py
    0004_add_stories.py
tests/
  conftest.py             session DB setup, per-test truncation, TestClient
  test_health.py
  test_sources.py
  test_ingestion.py
  test_normalization.py
```

---

## See also

- [Roadmap](docs/roadmap.md)
- [Architecture](docs/architecture.md)
