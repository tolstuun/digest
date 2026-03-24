# Roadmap

## Phase 0 — Bootstrap ✅

Goal: runnable service with database integration and source registry.

- FastAPI application skeleton
- PostgreSQL integration
- Health endpoint
- `sources` table and CRUD API (`GET /sources`, `POST /sources`)
- Alembic migrations
- Dockerfile + Docker Compose
- CI with tests

## Phase 1 — Source registry hardening + ingestion foundation ✅ (current)

Goal: extend the source registry for ingestion management and prove the basic ingest path shape.

- Extended `sources` model with ingestion fields: `parser_type`, `poll_frequency_minutes`, `last_polled_at`, `last_success_at`, `last_error`, `section_scope`
- `GET /sources/{id}` and `PATCH /sources/{id}` endpoints
- `raw_items` table: raw ingest store with `(source_id, content_hash)` deduplication
- RSS ingestion module (`app/ingestion/rss.py`): deterministic feedparser-based parsing
- `ingest_source()` service: fetch → parse → persist new raw items → update source state
- `POST /admin/sources/{id}/ingest`: manual trigger for one-source ingestion
- Idempotent: repeated runs skip already-stored items
- Migrations 0002 (source fields) and 0003 (raw_items table)

## Phase 2 — Normalization and clustering foundation

Goal: turn raw items into structured stories and detect obvious duplicates.

- `stories` table (normalized representation of a raw item: title, url, source_id, published_at)
- Normalization worker: `raw_items` → `stories`
- `story_clusters` table (groups of related stories)
- Basic deduplication by URL
- Source polling config: `last_fetched_at`, scheduled polling loop

## Phase 3 — Enrichment and scoring

Goal: add structured facts and editorial signals to stories.

- LLM enrichment worker: extract entities, category, canonical summary
- `entities` table (companies, people, events)
- `sections` table (configurable digest sections)
- Story-to-section relevance scoring (LLM-assisted)
- Priority/relevance score per story per section

## Phase 4 — Digest assembly and publishing

Goal: assemble and publish the first real digest.

- `digest_runs` table (one row per daily run)
- `digest_entries` table (selected stories per run, ranked)
- Digest assembly worker: select top stories per section
- HTML page renderer (daily digest web page)
- Telegram publisher (sends link to the page)
- Smoke test after publish

## Future sections

After Phase 4, add digest sections one by one:
- Major incidents
- Regulation and compliance
- Vendor launches and product news
- Conferences and events
- Curated long reads
