# Architecture

## System overview

Security Digest is a **modular monolith with a worker-based pipeline**.

```
┌──────────────────────────────────────────────────────────────────┐
│                      FastAPI Application                         │
│                                                                  │
│  GET /health                                                     │
│  GET|POST /sources/    GET|PATCH /sources/{id}                   │
│  GET /stories/         GET /stories/{id}   GET /stories/{id}/facts│
│  GET /event-clusters/  GET /event-clusters/{id}                  │
│  GET /event-clusters/{id}/assessment                             │
│  GET /digests/         GET /digests/{id}                         │
│  GET /digest-pages/    GET /digest-pages/{slug}                  │
│  GET /digest-publications/  GET /digest-publications/{id}         │
│  POST /admin/sources/{id}/ingest|normalize                       │
│  POST /admin/stories/{id}/extract-facts|cluster-event            │
│  POST /admin/event-clusters/{id}/assess                          │
│  POST /admin/digests/assemble                                    │
│  POST /admin/digests/{id}/render                                 │
│  POST /admin/digest-pages/{id}/publish-telegram                  │
│  GET|POST /ui/* (server-rendered ops HTML UI)                    │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                         PostgreSQL                               │
│                                                                  │
│  sources    raw_items    stories    story_facts    event_clusters │
│  event_cluster_assessments  digest_runs  digest_entries          │
│  digest_pages   digest_publications                              │
│  (planned) entities  sections  job_runs                          │
└──────────────────────────────────────────────────────────────────┘
```

**Pipeline stages** (✅ = implemented):

```
[sources] → ingest ✅ → raw_items
          → normalize ✅ → stories
          → extract-facts ✅ → story_facts
          → cluster-event ✅ → event_clusters (stories.event_cluster_id)
          → assess ✅ → event_cluster_assessments (rule_score + llm_score → final_score)
          → assemble digest ✅ → digest_runs + digest_entries
          → render HTML ✅ → digest_pages (slug, html_content)
          → publish Telegram ✅ → digest_publications
```

## Implemented components

### Phase 0 — Bootstrap

| Component               | Description                                              |
|-------------------------|----------------------------------------------------------|
| `app/main.py`           | FastAPI app; lifespan logging; router registration       |
| `app/config.py`         | YAML config loader; `APP_CONFIG_PATH` selects file; YAML-only |
| `app/database.py`       | SQLAlchemy engine, session factory, `Base`, `get_db`     |
| `app/models/source.py`  | `Source` ORM model                                       |
| `app/schemas/source.py` | `SourceCreate` / `SourcePatch` / `SourceOut`             |
| `app/routers/health.py` | `GET /health`                                            |
| `app/routers/sources.py`| `GET|POST /sources/`, `GET|PATCH /sources/{id}`          |

### Phase 1 — Ingestion

| Component                  | Description                                              |
|----------------------------|----------------------------------------------------------|
| `app/models/raw_item.py`   | `RawItem` model: raw ingest store, dedup constraint      |
| `app/ingestion/rss.py`     | Pure RSS/Atom parsing via feedparser; no DB, no LLM      |
| `app/ingestion/service.py` | `ingest_source()` — fetch → persist raw items → state   |
| `app/routers/admin.py`     | `POST /admin/sources/{id}/ingest`                        |

### Phase 2A — Normalization

| Component                      | Description                                          |
|--------------------------------|------------------------------------------------------|
| `app/models/story.py`          | `Story` ORM model: normalized form of a raw item     |
| `app/schemas/story.py`         | `StoryOut` Pydantic schema                           |
| `app/normalization/urls.py`    | `canonicalize_url()` — pure function, no DB, no LLM  |
| `app/normalization/service.py` | `normalize_raw_item()` — idempotent, returns (story, created) |
| `app/routers/stories.py`       | `GET /stories/`, `GET /stories/{id}`                 |
| `app/routers/admin.py`         | `POST /admin/sources/{id}/normalize` (extended)      |

### Phase 2B — LLM Fact Extraction

| Component                       | Description                                                        |
|---------------------------------|--------------------------------------------------------------------|
| `app/models/story_facts.py`     | `StoryFacts` ORM model: extracted facts per story                  |
| `app/extraction/schemas.py`     | `StoryInput` dataclass; `ExtractionResult` with `Literal` event_type |
| `app/extraction/llm.py`         | `extract_facts_llm()` — single Anthropic tool-use LLM boundary     |
| `app/extraction/service.py`     | `extract_story_facts()` — idempotent upsert, stores model + output |
| `app/schemas/story_facts.py`    | `StoryFactsOut` Pydantic schema                                    |
| `app/routers/stories.py`        | `GET /stories/{id}/facts` (extended)                               |
| `app/routers/admin.py`          | `POST /admin/stories/{id}/extract-facts` (extended)                |

### Phase 3A — Event Clustering

| Component                        | Description                                                       |
|----------------------------------|-------------------------------------------------------------------|
| `app/models/event_cluster.py`    | `EventCluster` ORM model: one cluster per unique event key        |
| `app/clustering/rules.py`        | `build_cluster_key()` — pure function; deterministic; no LLM      |
| `app/clustering/service.py`      | `cluster_story()` — idempotent assign/create; first=representative |
| `app/schemas/event_cluster.py`   | `EventClusterOut` Pydantic schema (incl. story_count, story_ids)  |
| `app/routers/event_clusters.py`  | `GET /event-clusters/`, `GET /event-clusters/{id}`                |
| `app/routers/admin.py`           | `POST /admin/stories/{id}/cluster-event` (extended)               |

### Phase 3B — Editorial Scoring

| Component                                  | Description                                                           |
|--------------------------------------------|-----------------------------------------------------------------------|
| `app/models/event_cluster_assessment.py`   | `EventClusterAssessment` ORM model: one assessment per cluster        |
| `app/scoring/schemas.py`                   | `ClusterInput` dataclass; `ClusterAssessment` Pydantic model          |
| `app/scoring/rules.py`                     | `compute_rule_score()` — deterministic pre-score; weights in code     |
| `app/scoring/llm.py`                       | `assess_cluster_llm()` — single Anthropic tool-use LLM boundary       |
| `app/scoring/service.py`                   | `assess_cluster()` — combines scores; idempotent upsert               |
| `app/schemas/event_cluster_assessment.py`  | `EventClusterAssessmentOut` Pydantic schema                           |
| `app/routers/event_clusters.py`            | `GET /event-clusters/{id}/assessment` (extended)                      |
| `app/routers/admin.py`                     | `POST /admin/event-clusters/{id}/assess` (extended)                   |

### Phase 4A — Digest Assembly

| Component                      | Description                                                              |
|--------------------------------|--------------------------------------------------------------------------|
| `app/models/digest_run.py`     | `DigestRun` ORM model: one run per (digest_date, section_name)           |
| `app/models/digest_entry.py`   | `DigestEntry` ORM model: one entry per included cluster; fields materialized at assembly |
| `app/digest/service.py`        | `assemble_digest()` — deterministic, no LLM; candidate selection + materialization |
| `app/schemas/digest.py`        | `DigestRunOut`, `DigestEntryOut`, `DigestRunDetail` Pydantic schemas     |
| `app/routers/digests.py`       | `GET /digests/`, `GET /digests/{id}`                                     |
| `app/routers/admin.py`         | `POST /admin/digests/assemble` (extended)                                |

### Phase 4B — HTML Rendering ✅

| Component                        | Description                                                              |
|----------------------------------|--------------------------------------------------------------------------|
| `app/models/digest_page.py`      | `DigestPage` ORM model: one page per digest run                          |
| `app/rendering/html.py`          | `render_digest_html()` — pure function; no DB, no LLM; HTML-escaping     |
| `app/rendering/html.py`          | `make_slug()`, `make_title()` — deterministic helpers                    |
| `app/rendering/service.py`       | `render_digest_page()` — idempotent upsert; stable page ID on re-render  |
| `app/schemas/digest_page.py`     | `DigestPageOut` Pydantic schema (metadata; no html_content)              |
| `app/routers/digest_pages.py`    | `GET /digest-pages/`, `GET /digest-pages/{slug}` (returns HTMLResponse)  |
| `app/routers/admin.py`           | `POST /admin/digests/{id}/render` (extended)                             |

### Phase 4C — Ops/Admin UI + YAML Config

| Component                            | Description                                                                 |
|--------------------------------------|-----------------------------------------------------------------------------|
| `app/config.py`                      | YAML config loader: structured sections; YAML-only, `APP_CONFIG_PATH` selects file |
| `config/settings.example.yaml`       | Committed template (localhost); real `config/settings.yaml` is git-ignored  |
| `config/settings.compose.yaml`       | Committed Docker Compose config (db hostname); no secrets                   |
| `app/routers/ui.py`                  | Server-rendered HTML UI under `/ui/` — Jinja2, no JS, no SPA               |
| `app/templates/base.html`            | Base layout with nav bar and flash message rendering                        |
| `app/templates/ui/dashboard.html`    | Counts for all pipeline tables + recent source errors                       |
| `app/templates/ui/sources.html`      | Sources table with Ingest + Normalize action buttons                        |
| `app/templates/ui/event_clusters.html` | Clusters table with assessment status, score + Assess button              |
| `app/templates/ui/digests.html`      | Digest runs table with Assemble form + Render + Publish TG + page link      |
| `app/templates/ui/config.html`       | Read-only config view; secrets masked via `mask_secret` Jinja2 filter       |

### Phase 4D — Telegram Publishing

| Component                              | Description                                                               |
|----------------------------------------|---------------------------------------------------------------------------|
| `app/models/digest_publication.py`     | `DigestPublication` ORM model: one row per (page, channel, target)        |
| `app/publishing/telegram.py`           | `build_message_text()` + `send_telegram_message()` — narrow HTTP boundary |
| `app/publishing/service.py`            | `publish_to_telegram()` — idempotent upsert; YAML config; public URL      |
| `app/schemas/digest_publication.py`    | `DigestPublicationOut` Pydantic schema                                    |
| `app/routers/digest_publications.py`   | `GET /digest-publications/`, `GET /digest-publications/{id}`              |
| `app/routers/admin.py`                 | `POST /admin/digest-pages/{id}/publish-telegram` (extended)               |

## Database schema (current)

### `sources`

| Column                 | Type          | Notes                                         |
|------------------------|---------------|-----------------------------------------------|
| id                     | UUID PK       |                                               |
| name                   | varchar(255)  | required                                      |
| type                   | varchar(50)   | `rss`/`api`/`html`/`manual`/`newsletter`      |
| url                    | varchar(2048) |                                               |
| enabled                | boolean       | default true                                  |
| tags                   | jsonb         |                                               |
| language               | varchar(10)   |                                               |
| geography              | varchar(100)  |                                               |
| priority               | integer       | default 0                                     |
| notes                  | text          |                                               |
| parser_type            | varchar(50)   |                                               |
| poll_frequency_minutes | integer       |                                               |
| last_polled_at         | timestamptz   | set by ingestion                              |
| last_success_at        | timestamptz   |                                               |
| last_error             | text          |                                               |
| section_scope          | jsonb         | list of section names                         |
| created_at             | timestamptz   |                                               |
| updated_at             | timestamptz   |                                               |

### `raw_items`

| Column       | Type          | Notes                                              |
|--------------|---------------|----------------------------------------------------|
| id           | UUID PK       |                                                    |
| source_id    | UUID FK       | → sources.id CASCADE                               |
| external_id  | varchar(512)  | RSS GUID or item URL                               |
| content_hash | varchar(64)   | SHA-256; unique with source_id                     |
| title        | varchar(1024) |                                                    |
| url          | varchar(2048) |                                                    |
| published_at | timestamptz   |                                                    |
| raw_payload  | jsonb         | JSON-safe subset of feedparser entry               |
| fetched_at   | timestamptz   |                                                    |
| created_at   | timestamptz   |                                                    |

### `stories`

| Column            | Type          | Notes                                              |
|-------------------|---------------|----------------------------------------------------|
| id                | UUID PK       |                                                    |
| raw_item_id       | UUID FK       | → raw_items.id CASCADE; **unique** (1 story/item)  |
| source_id         | UUID FK       | → sources.id CASCADE                               |
| event_cluster_id  | UUID FK       | → event_clusters.id SET NULL; nullable             |
| title             | varchar(1024) |                                                    |
| url               | varchar(2048) | as-fetched URL                                     |
| canonical_url     | varchar(2048) | normalized URL (tracking params stripped)          |
| published_at      | timestamptz   |                                                    |
| normalized_at     | timestamptz   | when normalization ran                             |
| created_at        | timestamptz   |                                                    |
| updated_at        | timestamptz   |                                                    |

### `story_facts`

| Column                 | Type          | Notes                                         |
|------------------------|---------------|-----------------------------------------------|
| id                     | UUID PK       |                                               |
| story_id               | UUID FK       | → stories.id CASCADE; **unique** (1/story)    |
| model_name             | varchar(256)  | LLM model used for extraction                 |
| raw_model_output       | jsonb         | full structured output from LLM               |
| extraction_confidence  | float         | 0.0–1.0                                       |
| extracted_at           | timestamptz   |                                               |
| source_language        | varchar(16)   | ISO 639-1                                     |
| event_type             | varchar(64)   | one of 11 typed values                        |
| company_names          | jsonb         | list of strings                               |
| person_names           | jsonb         |                                               |
| product_names          | jsonb         |                                               |
| geography_names        | jsonb         |                                               |
| amount_text            | varchar(256)  |                                               |
| currency               | varchar(16)   |                                               |
| canonical_summary_en   | varchar(2048) |                                               |
| canonical_summary_ru   | varchar(2048) |                                               |
| created_at             | timestamptz   |                                               |
| updated_at             | timestamptz   |                                               |

### `event_clusters`

| Column                  | Type          | Notes                                        |
|-------------------------|---------------|----------------------------------------------|
| id                      | UUID PK       |                                              |
| cluster_key             | varchar(512)  | unique; deterministic from facts             |
| event_type              | varchar(64)   |                                              |
| representative_story_id | UUID          | first story assigned; not FK-constrained     |
| created_at              | timestamptz   |                                              |
| updated_at              | timestamptz   |                                              |

### `event_cluster_assessments`

| Column               | Type          | Notes                                              |
|----------------------|---------------|----------------------------------------------------|
| id                   | UUID PK       |                                                    |
| event_cluster_id     | UUID FK       | → event_clusters.id CASCADE; **unique** (1/cluster)|
| primary_section      | varchar(64)   | one of 5 section types                             |
| include_in_digest    | boolean       |                                                    |
| rule_score           | float         | 0.0–1.0; deterministic pre-score                   |
| llm_score            | float         | 0.0–1.0; LLM editorial score                       |
| final_score          | float         | `0.4 * rule_score + 0.6 * llm_score`               |
| why_it_matters_en    | text          | LLM-generated editorial note (English)             |
| why_it_matters_ru    | text          | LLM-generated editorial note (Russian)             |
| editorial_notes      | text          | additional LLM editorial context                   |
| model_name           | varchar(256)  | LLM model used for assessment                      |
| raw_model_output     | jsonb         | full structured output from LLM                    |
| assessed_at          | timestamptz   | when assessment ran                                |
| created_at           | timestamptz   |                                                    |
| updated_at           | timestamptz   |                                                    |

### `digest_runs`

| Column                    | Type         | Notes                                                    |
|---------------------------|--------------|----------------------------------------------------------|
| id                        | UUID PK      |                                                          |
| digest_date               | date         | not null; index                                          |
| section_name              | varchar(64)  | `companies_business` in Phase 4A                         |
| status                    | varchar(32)  | `assembled` (entries exist) or `empty` (no candidates)   |
| total_candidate_clusters  | integer      | all clusters matching date+section before include filter |
| total_included_clusters   | integer      | clusters actually included after filtering + limit       |
| generated_at              | timestamptz  | when assembly ran                                        |
| created_at                | timestamptz  |                                                          |
| updated_at                | timestamptz  |                                                          |

Unique constraint: `(digest_date, section_name)` — one run per date+section.

### `digest_entries`

| Column                | Type          | Notes                                                          |
|-----------------------|---------------|----------------------------------------------------------------|
| id                    | UUID PK       |                                                                |
| digest_run_id         | UUID FK       | → digest_runs.id CASCADE                                       |
| event_cluster_id      | UUID FK       | → event_clusters.id SET NULL; nullable (preserves history)     |
| rank                  | integer       | position within run (1 = highest score)                        |
| final_score           | float         | copied from assessment at assembly time                        |
| title                 | varchar(1024) | materialized from representative story                         |
| canonical_summary_en  | text          | materialized from story_facts                                  |
| canonical_summary_ru  | text          | materialized from story_facts                                  |
| why_it_matters_en     | text          | materialized from event_cluster_assessment                     |
| why_it_matters_ru     | text          | materialized from event_cluster_assessment                     |
| created_at            | timestamptz   |                                                                |
| updated_at            | timestamptz   |                                                                |

### `digest_pages`

| Column         | Type          | Notes                                                                  |
|----------------|---------------|------------------------------------------------------------------------|
| id             | UUID PK       |                                                                        |
| digest_run_id  | UUID FK       | → digest_runs.id CASCADE; **unique** (1/run)                           |
| slug           | varchar(256)  | unique; `{digest_date}-{section_name_with_dashes}`                     |
| title          | varchar(512)  | human-readable page title                                              |
| html_content   | text          | complete rendered HTML                                                 |
| rendered_at    | timestamptz   | when rendering ran                                                     |
| created_at     | timestamptz   |                                                                        |
| updated_at     | timestamptz   |                                                                        |

### `digest_publications`

| Column              | Type          | Notes                                                                  |
|---------------------|---------------|------------------------------------------------------------------------|
| id                  | UUID PK       |                                                                        |
| digest_page_id      | UUID FK       | → digest_pages.id CASCADE                                              |
| channel_type        | varchar(64)   | `telegram` (extensible)                                                |
| target              | varchar(256)  | Telegram chat_id                                                       |
| message_text        | text          | full message sent                                                      |
| provider_message_id | varchar(256)  | Telegram message_id returned by API                                    |
| status              | varchar(32)   | `sent` / `failed`                                                      |
| published_at        | timestamptz   | when the message was sent                                              |
| created_at          | timestamptz   |                                                                        |
| updated_at          | timestamptz   |                                                                        |

Unique constraint: `(digest_page_id, channel_type, target)` — one publication per channel per page.

## Normalization flow (Phase 2A)

```
POST /admin/sources/{id}/normalize
         │
         ▼
Load all RawItems for source
         │
         ▼  (for each raw_item)
normalize_raw_item(db, raw_item):
  1. Check if Story already exists for raw_item_id → return existing (idempotent)
  2. canonical_url = canonicalize_url(raw_item.url)
       - lowercase scheme + host
       - remove #fragment
       - strip: utm_source, utm_medium, utm_campaign, utm_term,
                utm_content, fbclid, gclid
       - all other params and path preserved
  3. INSERT story (raw_item_id, source_id, title, url, canonical_url,
                   published_at, normalized_at)
  4. COMMIT
         │
         ▼
Return {source_id, total, new, skipped}
```

## Key design decisions

**Sources are data, not code** — source definitions in DB, not hardcoded.

**Deterministic first, LLM second** — ingestion and normalization are purely deterministic. No LLM touches data until enrichment (Phase 3).

**One raw_item → at most one story** — enforced by unique constraint on `stories.raw_item_id`. The normalization service also checks before inserting.

**Store raw inputs** — `raw_items` preserves original payloads. Re-normalization never requires re-fetching.

**Pipeline stages are atomic and retryable** — each stage (ingest, normalize, extract-facts, cluster-event) is independently re-triggerable from the admin endpoint.

**One story → at most one event cluster** — enforced by `stories.event_cluster_id` (nullable FK). The clustering service checks before creating a new cluster.

**Deterministic clustering before fuzzy matching** — Phase 3A uses only exact key matching (lowercased, sorted company names + event type + amount + currency). Fuzzy/semantic matching is explicitly deferred.

**Two-stage scoring: rule pre-score + LLM editorial judgment** — Phase 3B computes a deterministic rule score first (event type, coverage, financial details, source priority), then calls the LLM for editorial assessment. The final score weights the LLM score higher (0.6) because it captures contextual editorial relevance that rules cannot. The weights are explicit in code, not hidden in prompts.

**Digest entries materialize display data at assembly time** — DigestEntry copies title, summaries, and editorial notes from source tables at assembly time. This preserves digest history even if upstream data changes or clusters are deleted. Trade-off: entries may become stale; reassembling for the same date rebuilds from current upstream state.

## Technology choices

| Concern     | Choice              | Reason                                         |
|-------------|---------------------|------------------------------------------------|
| Framework   | FastAPI             | Typed, fast, OpenAPI                           |
| ORM         | SQLAlchemy 2.x      | Explicit, good migration tooling               |
| Migrations  | Alembic             | Standard SQLAlchemy companion                  |
| Database    | PostgreSQL 16       | Reliable, JSONB, future FTS                    |
| Validation  | Pydantic v2         | Fast, typed, FastAPI native                    |
| Config      | YAML only           | YAML file; APP_CONFIG_PATH selects file only   |
| UI templates| Jinja2              | Server-rendered HTML ops UI; no JS/SPA         |
| RSS parsing | feedparser          | Handles RSS/Atom/malformed feeds               |
| Runtime     | Docker Compose      | Reproducible local + server environments       |
| CI/CD       | GitHub Actions      | Simple, repository-native                      |

## ADR-011: YAML-only runtime configuration

**Decision:** All runtime configuration is read exclusively from a YAML file. The only environment variable accepted by the config loader is `APP_CONFIG_PATH`, which selects which file to load. No runtime values (database URL, API keys, Telegram tokens, etc.) are read from environment variables.

**Config file selection order:**
1. `config_path` argument to `load_settings()` (used in tests)
2. `APP_CONFIG_PATH` environment variable
3. Default: `config/settings.yaml`

**Committed config files:**
- `config/settings.example.yaml` — template for human reference and CI use (`localhost:5432`)
- `config/settings.compose.yaml` — docker-compose defaults (`db:5432`); no secrets; committed

**Git-ignored config files:** `config/settings.yaml`, `config/settings.local.yaml`

**Reason:** A YAML file with named sections is explicit, reviewable, and version-controllable without committing secrets. Allowing env var overrides for individual values creates a second implicit config surface — values can come from either YAML or env, making the effective config hard to reason about. With YAML-only policy, the config in the file is the config; there are no hidden overrides.

**CI note:** CI workflow sets `APP_CONFIG_PATH=config/settings.example.yaml` (which uses `localhost:5432`, matching the CI postgres service). No `DATABASE_URL` env var is needed or read.

## ADR-012: Server-rendered HTML UI under `/ui/` prefix

**Decision:** The internal ops UI lives under `/ui/` (not `/admin/`) using Jinja2 templates + minimal inline CSS. No JavaScript framework, no SPA, no separate frontend build.

**Reason:** The existing JSON admin API lives under `/admin/*` (POST endpoints). Using `/ui/` avoids route conflicts and makes the distinction clear: `/admin/*` = JSON operations API, `/ui/*` = HTML ops UI. Server-rendered HTML with Jinja2 is fast to implement, easy to test, requires no build tooling, and is appropriate for an internal operational tool.

## ADR-001: Sync over async for DB access

**Decision:** Synchronous SQLAlchemy sessions and sync FastAPI handlers.
**Reason:** Pipeline is I/O-bound at the network level (fetching RSS), not at DB level. Sync is easier to test and debug.

## ADR-002: UUID primary keys

**Decision:** UUID PKs for all tables.
**Reason:** Rows are referenced across tables and environments. UUIDs avoid collisions during migration or data merges.

## ADR-003: Content hash for raw item deduplication

**Decision:** Deduplicate `raw_items` by `SHA-256(external_id or url or title)` per source.
**Reason:** RSS GUIDs are the most reliable dedup key; fallback covers sources without GUIDs. Hash maps cleanly to a DB unique constraint.

## ADR-004: Admin endpoint as ingestion/normalization trigger

**Decision:** `POST /admin/sources/{id}/ingest` and `POST /admin/sources/{id}/normalize` instead of CLI scripts.
**Reason:** Easier to test (standard HTTP), consistent with the API pattern, usable from Docker without entering a shell.

## ADR-005: Conservative URL canonicalization

**Decision:** Strip only UTM parameters and click-tracking IDs (fbclid, gclid). No path manipulation. Return original string on error.
**Reason:** Over-aggressive URL normalization causes false deduplication across genuinely different content. The goal is to make the same article URL comparable across slightly different referral variants, not to parse URLs cleverly.

## ADR-006: Deterministic cluster key from structured facts

**Decision:** Cluster key = `"{event_type}:{sorted_lowercased_companies}[:{amount_text}][:{currency}]"`. No fuzzy matching, no LLM, no NLP.
**Reason:** Fuzzy matching introduces false positives (two unrelated funding rounds at the same company in the same period). Starting conservative (exact match) makes the clustering behavior fully predictable, testable, and auditable. Fuzzy matching can be layered on in a later phase once the pipeline is proven.

## ADR-007: representative_story_id is not a FK constraint

**Decision:** `event_clusters.representative_story_id` stores a UUID but is not declared as a FK to `stories.id`.
**Reason:** Adding a FK here creates a circular dependency (`stories → event_clusters → stories`), which complicates migrations and the SQLAlchemy metadata dependency graph. The representative story should be treated as a soft reference — if the story is deleted, the cluster remains valid. Application code must handle a missing representative gracefully.

## ADR-008: Two-stage scoring with explicit weights in code

**Decision:** `final_score = 0.4 * rule_score + 0.6 * llm_score`. Weights are hardcoded constants in `app/scoring/service.py`, not in prompts.
**Reason:** Keeping weights explicit in code (not hidden in the LLM prompt) makes the scoring formula auditable, testable, and adjustable without touching prompt text. The LLM receives a higher weight (0.6) because editorial context (geopolitical significance, novelty, audience relevance) cannot be fully captured by deterministic rules. The rule pre-score is still computed first to provide a stable baseline that the LLM score is combined with, not replaced by.

## Scoring flow (Phase 3B)

```
POST /admin/event-clusters/{id}/assess
         │
         ▼
assess_cluster(db, cluster):
  1. Load all stories linked to cluster
  2. Load representative story's facts (for company_names, summaries, etc.)
  3. Compute max source priority across linked stories
  4. rule_score = compute_rule_score(event_type, story_count, has_amount,
                                     has_currency, max_source_priority)
       weights: event_type_base + coverage_bonus + financial_bonus + priority_bonus
  5. cluster_input = ClusterInput(cluster_id, event_type, story_count,
                                   company_names, amount_text, currency,
                                   canonical_summary_en, canonical_summary_ru,
                                   representative_title)
  6. llm_result = assess_cluster_llm(cluster_input)
       → Anthropic tool-use; returns ClusterAssessment:
         primary_section, llm_score, include_in_digest,
         why_it_matters_en, why_it_matters_ru, editorial_notes
  7. final_score = round(0.4 * rule_score + 0.6 * llm_score, 4)
  8. Upsert EventClusterAssessment (idempotent)
         │
         ▼
Return {cluster_id, primary_section, rule_score, llm_score, final_score,
        include_in_digest, created}
```

## Digest assembly flow (Phase 4A)

```
POST /admin/digests/assemble  {digest_date, max_entries?}
         │
         ▼
assemble_digest(db, digest_date, section_name, max_entries):
  1. Delete existing DigestRun for (digest_date, section_name) if any
     (cascade-deletes all DigestEntry rows — idempotent delete-and-rebuild)
  2. Load all EventClusterAssessments where primary_section = section_name
  3. For each assessment, load EventCluster + representative Story + StoryFacts
  4. Filter by effective_date:
       if rep_story.published_at is not None → use rep_story.published_at.date()
       else → use event_cluster.created_at.date()
     Keep only clusters where effective_date == digest_date
  5. total_candidate_clusters = len(above) [regardless of include_in_digest]
  6. Filter to include_in_digest=True
  7. Sort by final_score descending
  8. Slice to max_entries (default 20)
  9. Create DigestRun (status="assembled" if entries exist, "empty" otherwise)
  10. Create DigestEntry for each: copy title, summaries, why_it_matters from source
         │
         ▼
Return {digest_run_id, digest_date, section_name, total_candidates, total_included, created}
```

## ADR-009: Delete-and-rebuild as digest assembly idempotency policy

**Decision:** Repeated calls to `assemble_digest()` for the same (digest_date, section_name) delete the existing DigestRun (cascade-deleting all DigestEntry rows) and build a fresh run from current upstream state.

**Reason:** The alternative (in-place update of entries) requires diffing and merging entry lists, which adds complexity and can leave orphaned entries. Delete-and-rebuild is simpler, always produces a consistent state, and is safe to retry after failures. The trade-off is that the run ID changes on each rebuild — callers holding a run ID should re-query if they need the current run for a given date.

## ADR-010: Upsert as rendering idempotency policy

**Decision:** Repeated calls to `render_digest_page()` for the same DigestRun update the existing DigestPage row in place (`slug`, `title`, `html_content`, `rendered_at`). The page ID remains stable across re-renders.

**Reason:** Unlike digest assembly (where delete-and-rebuild is correct because entry lists can grow or shrink), rendering is a pure transformation: same run data always produces the same output. Upsert avoids breaking any caller that holds a page ID or slug. The slug is based on the run's (date, section) so it cannot collide with other runs.

## HTML rendering flow (Phase 4B)

```
POST /admin/digests/{digest_run_id}/render
         │
         ▼
render_digest_page(db, run):
  1. Load DigestEntry rows for run, ordered by rank
  2. html = render_digest_html(run, entries)   ← pure function
       - make_title(run): "Security Digest — {date} — {Section Name}"
       - make_slug(run): "{date}-{section-name-with-dashes}"
       - for each entry: render rank, title, score, summaries (EN+RU),
                          why-it-matters (EN+RU); HTML-escape all content
  3. Check for existing DigestPage with digest_run_id == run.id
  4. If none: INSERT DigestPage(slug, title, html_content, rendered_at)
     If exists: UPDATE slug, title, html_content, rendered_at (stable id)
  5. COMMIT
         │
         ▼
Return {digest_page_id, digest_run_id, slug, rendered_at, created}

Public read:
  GET /digest-pages/{slug} → HTMLResponse(html_content, content_type="text/html")
```
