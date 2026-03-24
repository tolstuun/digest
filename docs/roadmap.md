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

## Phase 2A — Normalization foundation ✅

Goal: turn raw_items into normalized stories using deterministic code only.

- `stories` table: id, raw_item_id (unique FK), source_id, title, url, canonical_url, published_at, normalized_at, created_at, updated_at
- `canonicalize_url()`: lowercase scheme/host, remove fragment, strip UTM/fbclid/gclid params
- `normalize_raw_item(db, raw_item)`: idempotent, returns (story, created), no LLM
- `GET /stories/`, `GET /stories/{id}`
- `POST /admin/sources/{id}/normalize` — normalize all raw_items for a source
- Migration 0004

## Phase 2B — LLM fact extraction ✅

Goal: extract structured facts from each story using an LLM.

- `story_facts` table: one row per story, upsert on re-extraction
- `ExtractionResult` schema: typed `event_type` (11 values), confidence bounds
- Anthropic tool-use (`claude-haiku-4-5-20251001` by default) for structured JSON output
- `extract_story_facts()` service: idempotent upsert, stores model name + raw output
- `GET /stories/{id}/facts`, `POST /admin/stories/{id}/extract-facts`
- Migration 0005

## Phase 3A — Event clustering ✅

Goal: group stories describing the same real-world event using extracted structured facts.

- `event_clusters` table: id, cluster_key (unique), event_type, representative_story_id
- `stories.event_cluster_id`: nullable FK; one story → at most one cluster
- `build_cluster_key()`: pure deterministic function — `event_type + sorted(company_names) + amount_text + currency`
- Stories with `event_type` in (`unknown`, `other`) or no company names are not clustered
- `cluster_story()`: idempotent; joins existing cluster or creates new one; first story becomes representative
- `GET /event-clusters/`, `GET /event-clusters/{id}`
- `POST /admin/stories/{id}/cluster-event`
- Migration 0006

**Intentionally not in this phase:** fuzzy/semantic matching, NLP, LLM-assisted clustering, publication date proximity, scoring.

## Phase 3B — Editorial scoring ✅ *(current)*

Goal: add editorial signals to event clusters — rule-based pre-score + LLM editorial judgment.

- `event_cluster_assessments` table: one row per cluster, upserted on reassessment
- `compute_rule_score()`: deterministic pre-score; weights visible in code (event_type base, coverage bonus, financial bonus, source priority bonus)
- `assess_cluster_llm()`: single Anthropic tool-use LLM boundary; returns `primary_section`, `llm_score`, `include_in_digest`, bilingual editorial notes
- `assess_cluster()`: combines scores — `final_score = 0.4 * rule_score + 0.6 * llm_score`; idempotent upsert
- `GET /event-clusters/{id}/assessment`
- `POST /admin/event-clusters/{id}/assess`
- Migration 0007

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
