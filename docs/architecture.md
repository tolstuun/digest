# Architecture

## System overview

Security Digest is a **modular monolith with a worker-based pipeline**.

```
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI Application                       │
│                                                              │
│  GET /health    GET /sources    POST /sources                │
│  GET /sources/{id}   PATCH /sources/{id}                     │
│  POST /admin/sources/{id}/ingest                             │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│                      PostgreSQL                              │
│                                                              │
│  sources        raw_items                                    │
│  (planned) stories   story_clusters   entities               │
│  (planned) sections  digest_runs  digest_entries  job_runs   │
└──────────────────────────────────────────────────────────────┘
```

**Pipeline stages** (Phase 1 implemented, rest planned):

```
[sources] → ingest (✅ Phase 1) → raw_items
          → normalize (Phase 2) → stories
          → cluster (Phase 2) → story_clusters
          → enrich (Phase 3) → entities + scores
          → assign to sections (Phase 3)
          → assemble digest_run (Phase 4)
          → render HTML (Phase 4)
          → publish Telegram (Phase 4)
```

Each stage is an independent, retryable entrypoint.

## Implemented components

### Phase 0

| Component               | Description                                              |
|-------------------------|----------------------------------------------------------|
| `app/main.py`           | FastAPI app; lifespan logging; router registration       |
| `app/config.py`         | Settings from environment via `pydantic-settings`        |
| `app/database.py`       | SQLAlchemy engine, session factory, `Base`, `get_db`     |
| `app/models/source.py`  | `Source` ORM model                                       |
| `app/schemas/source.py` | `SourceCreate` / `SourcePatch` / `SourceOut` Pydantic v2 |
| `app/routers/health.py` | `GET /health`                                            |
| `app/routers/sources.py`| `GET /sources/`, `GET /sources/{id}`, `POST`, `PATCH`    |
| `alembic/`              | Migration runner; reads `DATABASE_URL` from env          |

### Phase 1

| Component                  | Description                                              |
|----------------------------|----------------------------------------------------------|
| `app/models/raw_item.py`   | `RawItem` model: raw ingest store with dedup constraint  |
| `app/ingestion/rss.py`     | Pure RSS/Atom parsing via feedparser; no DB, no LLM      |
| `app/ingestion/service.py` | `ingest_source(db, source)` — orchestrates ingest flow   |
| `app/routers/admin.py`     | `POST /admin/sources/{id}/ingest` — manual trigger       |

## Database schema (current)

### `sources`

| Column                  | Type          | Notes                                        |
|-------------------------|---------------|----------------------------------------------|
| id                      | UUID PK       | auto-generated                               |
| name                    | varchar(255)  | required                                     |
| type                    | varchar(50)   | `rss`/`api`/`html`/`manual`/`newsletter`     |
| url                     | varchar(2048) | nullable                                     |
| enabled                 | boolean       | default true                                 |
| tags                    | jsonb         | list of strings                              |
| language                | varchar(10)   | e.g. `en`                                    |
| geography               | varchar(100)  | e.g. `us`                                    |
| priority                | integer       | default 0                                    |
| notes                   | text          | nullable                                     |
| parser_type             | varchar(50)   | e.g. `feedparser`; nullable                  |
| poll_frequency_minutes  | integer       | nullable                                     |
| last_polled_at          | timestamptz   | set by ingestion; nullable                   |
| last_success_at         | timestamptz   | set on successful fetch; nullable            |
| last_error              | text          | last error message; nullable                 |
| section_scope           | jsonb         | list of section names; nullable              |
| created_at              | timestamptz   | auto-set                                     |
| updated_at              | timestamptz   | auto-updated                                 |

### `raw_items`

| Column       | Type          | Notes                                              |
|--------------|---------------|----------------------------------------------------|
| id           | UUID PK       | auto-generated                                     |
| source_id    | UUID FK       | → sources.id (CASCADE DELETE)                      |
| external_id  | varchar(512)  | RSS GUID or item URL; nullable                     |
| content_hash | varchar(64)   | SHA-256 hex; unique with source_id for dedup       |
| title        | varchar(1024) | nullable                                           |
| url          | varchar(2048) | nullable                                           |
| published_at | timestamptz   | nullable                                           |
| raw_payload  | jsonb         | JSON-safe subset of feedparser entry               |
| fetched_at   | timestamptz   | when this fetch occurred                           |
| created_at   | timestamptz   | auto-set                                           |

Unique constraint: `(source_id, content_hash)` — ensures idempotent ingestion.

## Ingestion flow (Phase 1)

```
POST /admin/sources/{id}/ingest
       │
       ▼
admin router: load Source from DB, call ingest_source()
       │
       ▼
ingest_source(db, source):
  1. Validate: enabled? type == rss? url set?
  2. parse_feed(source.url)  ← feedparser, pure function
  3. For each RawFeedItem:
       content_hash = SHA-256(external_id or url or title)
       if (source_id, content_hash) exists → skip
       else → INSERT into raw_items
  4. UPDATE source: last_polled_at, last_success_at / last_error
  5. COMMIT
       │
       ▼
Return: {fetched, new, skipped, error}
```

## Key design decisions

### Sources are data, not code
Source definitions live in the `sources` table. Adding a new source requires a DB insert, not a code change.

### Deterministic first, LLM second
Ingestion and deduplication are purely deterministic. `app/ingestion/rss.py` has no LLM usage. LLM is reserved for normalization/enrichment in later phases.

### One story can belong to multiple sections
The `digest_entries` join table (Phase 4) associates stories to sections per run.

### Store raw inputs
`raw_items` preserves the original payload. Re-processing never requires re-fetching the source.

### Pipeline stages are atomic and retryable
Each stage reads from and writes to the DB. Ingestion can be re-triggered at any time.

## Technology choices

| Concern     | Choice              | Reason                                         |
|-------------|---------------------|------------------------------------------------|
| Framework   | FastAPI             | Typed, fast, excellent OpenAPI support         |
| ORM         | SQLAlchemy 2.x      | Mature, explicit, good migration tooling       |
| Migrations  | Alembic             | Standard SQLAlchemy companion                  |
| Database    | PostgreSQL 16       | Reliable, JSONB, future FTS                    |
| Validation  | Pydantic v2         | Fast, typed, native FastAPI integration        |
| Config      | pydantic-settings   | Reads from env / .env, typed                   |
| RSS parsing | feedparser          | Mature, handles RSS/Atom/malformed feeds       |
| Runtime     | Docker Compose      | Reproducible local and server environments     |
| CI/CD       | GitHub Actions      | Simple, repository-native                      |

## ADR-001: Sync over async for DB access

**Decision:** Use synchronous SQLAlchemy sessions and sync FastAPI route handlers.

**Reason:** The pipeline is I/O-bound at the network level (fetching RSS), not at the DB level. Async SQLAlchemy adds complexity with no measurable benefit at this scale.

## ADR-002: UUID primary keys

**Decision:** UUID (PostgreSQL native) as PK for `sources` and `raw_items`.

**Reason:** Rows may be referenced across tables and environments. UUIDs avoid collisions during migration or merges.

## ADR-003: Content hash for deduplication

**Decision:** Deduplicate `raw_items` by `SHA-256(external_id or url or title)` per source.

**Reason:** RSS GUIDs are the most reliable dedup key. Fallback to URL or title handles sources that omit GUIDs. Hash-based approach avoids storing full content for comparison and maps cleanly to a unique DB constraint.

## ADR-004: Admin endpoint over CLI for ingestion trigger

**Decision:** Expose ingestion via `POST /admin/sources/{id}/ingest` rather than a CLI script.

**Reason:** Easier to test (standard HTTP), easier to use from Docker, consistent with the existing API pattern. The underlying `ingest_source(db, source)` service function is independently callable from any entrypoint (CLI, scheduler) when needed.
