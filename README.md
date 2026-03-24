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

**Phase 2A — normalization foundation** ✅
- `stories` table — one story per raw item
- Deterministic URL canonicalization (lowercase host/scheme, strip UTM/fbclid/gclid, remove fragment)
- `normalize_raw_item()` service — idempotent, no LLM
- `GET /stories/`, `GET /stories/{id}`
- `POST /admin/sources/{id}/normalize` — normalize all raw items for a source

**Phase 2B — LLM fact extraction** ✅
- `story_facts` table — one row per story, upsert on re-extraction
- `ExtractionResult` schema — typed event_type (11 values), confidence bounds
- Anthropic tool-use for structured JSON output (`claude-haiku-4-5-20251001`)
- `extract_story_facts()` service — idempotent upsert, stores model name + raw output
- `GET /stories/{id}/facts`, `POST /admin/stories/{id}/extract-facts`

**Phase 3A — Event clustering** ✅
- `event_clusters` table — one cluster per unique event (keyed by type + companies + amount + currency)
- `stories.event_cluster_id` — nullable FK; one story belongs to at most one cluster
- `build_cluster_key()` — pure deterministic function, no LLM, no fuzzy matching
- `cluster_story()` service — idempotent; creates or joins cluster; first story becomes representative
- `GET /event-clusters/`, `GET /event-clusters/{id}`, `POST /admin/stories/{id}/cluster-event`

**Phase 3B — Editorial scoring** ✅
- `event_cluster_assessments` table — one row per cluster (upserted on reassessment)
- `compute_rule_score()` — deterministic pre-score; weights visible in code (event_type, coverage, amount, source priority)
- `assess_cluster_llm()` — Anthropic tool-use boundary: returns `primary_section`, `llm_score`, `include_in_digest`, bilingual editorial notes
- `assess_cluster()` — combines scores: `final_score = 0.4 * rule_score + 0.6 * llm_score`; idempotent upsert
- `GET /event-clusters/{id}/assessment`, `POST /admin/event-clusters/{id}/assess`

**Phase 4A — Digest assembly foundation** ✅ *(current)*
- `digest_runs` table — one run per (date + section); unique constraint on (digest_date, section_name)
- `digest_entries` table — one entry per included cluster; materialized display fields copied at assembly time
- `assemble_digest()` service — fully deterministic, no LLM; selects assessed clusters for a date, filters by `include_in_digest=True` and `primary_section=companies_business`, sorts by `final_score` desc, limits to top 20 by default
- Date assignment rule: use representative story `published_at` if available; fall back to `event_cluster.created_at`
- Idempotent policy: delete-and-rebuild — repeated assembly for the same date+section deletes the old run and creates a fresh one
- `GET /digests/`, `GET /digests/{id}` — list and detail with entries in rank order
- `POST /admin/digests/assemble` — accepts `{digest_date, max_entries?}`

**Not yet implemented:** HTML rendering, Telegram publishing, schedulers, multi-section orchestration, fuzzy/semantic clustering.

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
1. Create a source:  POST /sources/
2. Ingest:           POST /admin/sources/{id}/ingest
3. Normalize:        POST /admin/sources/{id}/normalize
4. Extract facts:    POST /admin/stories/{id}/extract-facts
5. Cluster:          POST /admin/stories/{id}/cluster-event
6. Assess:           POST /admin/event-clusters/{id}/assess
7. Assemble digest:  POST /admin/digests/assemble
8. Read digest:      GET /digests/{id}
```

### Triggering digest assembly manually

```bash
# Assemble digest for a specific date (companies_business section)
curl -X POST http://localhost:8000/admin/digests/assemble \
  -H "Content-Type: application/json" \
  -d '{"digest_date": "2026-03-24"}'

# With custom entry limit
curl -X POST http://localhost:8000/admin/digests/assemble \
  -H "Content-Type: application/json" \
  -d '{"digest_date": "2026-03-24", "max_entries": 10}'

# List digest runs
curl http://localhost:8000/digests/

# Get digest detail
curl http://localhost:8000/digests/{digest_run_id}
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
| GET    | /stories/{id}/facts                | Get extracted facts for a story          |
| GET    | /event-clusters/                   | List all event clusters                  |
| GET    | /event-clusters/{id}               | Get event cluster detail + story ids     |
| GET    | /event-clusters/{id}/assessment    | Get editorial assessment for a cluster   |
| GET    | /digests/                          | List all digest runs                     |
| GET    | /digests/{id}                      | Get digest run detail with entries       |
| POST   | /admin/sources/{id}/ingest         | Trigger RSS ingestion for one source     |
| POST   | /admin/sources/{id}/normalize      | Normalize all raw items for one source   |
| POST   | /admin/stories/{id}/extract-facts  | Trigger LLM fact extraction for a story  |
| POST   | /admin/stories/{id}/cluster-event  | Assign story to an event cluster         |
| POST   | /admin/event-clusters/{id}/assess  | Trigger editorial assessment for cluster |
| POST   | /admin/digests/assemble            | Assemble digest for a date               |

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
  extraction/
    schemas.py            StoryInput dataclass, ExtractionResult Pydantic model
    llm.py                extract_facts_llm() — single Anthropic tool-use boundary
    service.py            extract_story_facts() — LLM call + upsert to story_facts
  clustering/
    rules.py              build_cluster_key() — pure deterministic function, no DB, no LLM
    service.py            cluster_story() — idempotent assign/create cluster
  scoring/
    schemas.py            ClusterInput dataclass, ClusterAssessment Pydantic model
    rules.py              compute_rule_score() — deterministic pre-score
    llm.py                assess_cluster_llm() — single Anthropic tool-use boundary
    service.py            assess_cluster() — combines scores, idempotent upsert
  digest/
    service.py            assemble_digest() — candidate selection, materialization, idempotent
alembic/
  versions/
    0001_initial_sources.py
    0002_source_ingestion_fields.py
    0003_add_raw_items.py
    0004_add_stories.py
    0005_add_story_facts.py
    0006_add_event_clusters.py
    0007_add_event_cluster_assessments.py
    0008_add_digest_runs_entries.py
tests/
  conftest.py             session DB setup, per-test truncation, TestClient
  test_health.py
  test_sources.py
  test_ingestion.py
  test_normalization.py
  test_extraction.py
  test_clustering.py
  test_scoring.py
  test_digest.py
```

---

## See also

- [Roadmap](docs/roadmap.md)
- [Architecture](docs/architecture.md)
