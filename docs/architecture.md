# Architecture

## System overview

Security Digest is a **modular monolith with a worker-based pipeline**.

```
┌─────────────────────────────────────────────────────┐
│                   FastAPI Application                │
│                                                     │
│  GET /health          GET /sources   POST /sources  │
│                                                     │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│                   PostgreSQL                        │
│                                                     │
│  sources   raw_items   stories   story_clusters     │
│  entities  sections    digest_runs  digest_entries  │
│  job_runs                                           │
└─────────────────────────────────────────────────────┘
```

**Pipeline stages** (planned, not yet implemented):

```
[sources] → ingest → raw_items
         → normalize → stories
         → cluster → story_clusters
         → enrich → entities + scores
         → assign to sections
         → assemble digest_run
         → render HTML
         → publish (Telegram)
```

Each stage is an independent, retryable worker entrypoint.

## Current implemented components (Phase 0)

| Component      | Description                                         |
|----------------|-----------------------------------------------------|
| `app/main.py`  | FastAPI app; startup logging; router registration   |
| `app/config.py`| Settings from environment via `pydantic-settings`   |
| `app/database.py` | SQLAlchemy engine, session factory, `Base`, `get_db` dependency |
| `app/models/source.py` | `Source` ORM model (SQLAlchemy 2.x mapped columns) |
| `app/schemas/source.py` | `SourceCreate` / `SourceOut` Pydantic v2 schemas |
| `app/routers/health.py` | `GET /health` |
| `app/routers/sources.py` | `GET /sources/`, `POST /sources/` |
| `alembic/`     | Migration runner; `0001` creates the `sources` table |

## Key design decisions

### Sources are data, not code
Source definitions live in the `sources` table. Adding a new source means inserting a row, not changing code. Parser type and polling config are stored fields.

### Deterministic first, LLM second
Ingestion, normalization, deduplication, and persistence use deterministic code. LLM is reserved for language-dependent tasks: classification, summarization, editorial scoring, and section assignment.

### One story can belong to multiple sections
The `digest_entries` join table associates stories to sections per run. There is no single-section assumption at the data model level.

### Pipeline stages are atomic and retryable
Each stage reads from and writes to the DB. Any stage can be rerun independently without re-ingesting from the source.

### Store raw inputs
`raw_items` preserves the original source payload. Re-processing (re-normalize, re-enrich) never requires re-fetching.

## Technology choices

| Concern        | Choice                | Reason                                         |
|----------------|-----------------------|------------------------------------------------|
| Framework      | FastAPI               | Typed, fast, excellent OpenAPI support         |
| ORM            | SQLAlchemy 2.x        | Mature, explicit, good migration tooling       |
| Migrations     | Alembic               | Standard companion to SQLAlchemy               |
| Database       | PostgreSQL 16         | Reliable, JSON support, good for future FTS    |
| Validation     | Pydantic v2           | Fast, typed, native FastAPI integration        |
| Config         | pydantic-settings     | Reads from env / .env, typed                   |
| Runtime        | Docker Compose        | Reproducible local and server environments     |
| CI/CD          | GitHub Actions        | Simple, repository-native                      |

## Intentionally excluded

- Kubernetes (too early)
- Kafka / message queues (overkill for this scale)
- Redis / Celery (not needed yet)
- Vector database (not needed until semantic search is required)
- Multiple repositories (single monorepo for now)

## ADR-001: Sync over async for DB access

**Decision:** Use synchronous SQLAlchemy sessions and sync FastAPI route handlers.

**Reason:** The digest pipeline is I/O-bound at the network level (fetching RSS/HTML), not at the DB level. Async SQLAlchemy adds significant complexity with no measurable benefit at this scale. Sync is easier to test, debug, and reason about. This can be revisited if DB-level concurrency becomes a bottleneck.

## ADR-002: UUID primary keys for sources

**Decision:** Use `UUID` (PostgreSQL native) as the primary key for `sources`.

**Reason:** Sources will eventually be referenced across multiple tables. UUIDs avoid collisions if rows are ever migrated between environments or merged from external systems.
