# Roadmap

## Phase 0 — Bootstrap ✅ (current)

Goal: runnable service with database integration and source registry.

- FastAPI application skeleton
- PostgreSQL integration
- Health endpoint
- `sources` table and CRUD API (`GET /sources`, `POST /sources`)
- Alembic migrations
- Dockerfile + Docker Compose
- CI with tests

## Phase 1 — Source registry + ingestion foundation

Goal: ingest content from real sources and store raw items.

- Complete source CRUD (PUT, DELETE, GET by ID)
- `raw_items` table (stores fetched payloads)
- RSS ingestion worker (fetches enabled RSS sources, writes raw_items)
- `job_runs` table for pipeline stage observability
- Idempotent fetch: skip already-ingested items by content hash
- Source polling config (interval, last_fetched_at)

## Phase 2 — Normalization and clustering foundation

Goal: turn raw items into structured stories and detect duplicates.

- `stories` table (normalized representation of a raw item)
- Normalization worker: extract title, url, published_at, source_id
- `story_clusters` table (groups of related stories)
- Basic clustering by URL/title similarity (deterministic, no LLM)
- Deduplication: merge stories pointing to the same URL

## Phase 3 — Enrichment and scoring

Goal: add structured facts and editorial signals to stories.

- LLM enrichment worker: extract entities, category, summary
- `entities` table (companies, people, events mentioned)
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
