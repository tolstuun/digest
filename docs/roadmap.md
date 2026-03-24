# Roadmap

## Phase 0 — Bootstrap ✅

Goal: runnable service with database integration and source registry.

- FastAPI application skeleton, PostgreSQL integration, health endpoint
- `sources` table, `GET /sources/`, `POST /sources/`
- Alembic migrations, Dockerfile, Docker Compose, CI with tests

## Phase 1 — Source registry hardening + ingestion foundation ✅

Goal: extend the source registry for ingestion management and prove the basic ingest path shape.

- Extended `sources` model (parser_type, poll_frequency_minutes, last_polled_at, last_success_at, last_error, section_scope)
- `GET /sources/{id}`, `PATCH /sources/{id}`
- `raw_items` table with `(source_id, content_hash)` deduplication
- RSS ingestion module (feedparser-based, deterministic)
- `ingest_source()` service, `POST /admin/sources/{id}/ingest`
- Migrations 0002, 0003

## Phase 2A — Normalization foundation ✅ (current)

Goal: turn raw_items into normalized stories using deterministic code only.

- `stories` table: id, raw_item_id (unique FK), source_id, title, url, canonical_url, published_at, normalized_at, created_at, updated_at
- `canonicalize_url()`: lowercase scheme/host, remove fragment, strip UTM/fbclid/gclid params
- `normalize_raw_item(db, raw_item)`: idempotent, returns (story, created), no LLM
- `GET /stories/`, `GET /stories/{id}`
- `POST /admin/sources/{id}/normalize` — normalize all raw_items for a source
- Migration 0004

## Phase 2B — Story deduplication (planned next)

Goal: detect and group stories covering the same event across different sources.

- `story_clusters` table (groups of related stories)
- Deduplication by canonical URL: stories sharing the same canonical_url are clustered
- Representative story selection per cluster
- `GET /story-clusters/`

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

## Future sections

After Phase 4, add digest sections one by one:
- Major incidents
- Regulation and compliance
- Vendor launches and product news
- Conferences and events
- Curated long reads
