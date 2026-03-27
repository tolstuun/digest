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
- Anthropic tool-use for structured JSON output; model configured via `llm.model_extraction` (default: `claude-haiku-4-5-20251001`)
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
- `assess_cluster_llm()` — Anthropic tool-use boundary: returns `primary_section`, `llm_score`, `include_in_digest`, bilingual editorial notes; model configured via `llm.model_scoring` (default: `claude-haiku-4-5-20251001`)
- `assess_cluster()` — combines scores: `final_score = 0.4 * rule_score + 0.6 * llm_score`; idempotent upsert
- `GET /event-clusters/{id}/assessment`, `POST /admin/event-clusters/{id}/assess`

**Phase 4A — Digest assembly foundation** ✅
- `digest_runs` table — one run per (date + section); unique constraint on (digest_date, section_name)
- `digest_entries` table — one entry per included cluster; materialized display fields copied at assembly time
- `assemble_digest()` service — fully deterministic, no LLM; selects assessed clusters for a date, filters by `include_in_digest=True` and `primary_section=companies_business`, sorts by `final_score` desc, limits to top 20 by default
- Date assignment rule: use representative story `published_at` if available; fall back to `event_cluster.created_at`
- Idempotent policy: delete-and-rebuild — repeated assembly for the same date+section deletes the old run and creates a fresh one
- `GET /digests/`, `GET /digests/{id}` — list and detail with entries in rank order
- `POST /admin/digests/assemble` — accepts `{digest_date, max_entries?}`

**Phase 4B — HTML rendering foundation** ✅
- `digest_pages` table — one rendered page per digest run; unique FK on digest_run_id
- `render_digest_html()` — pure function: no DB, no LLM; builds complete HTML from run + entries
- `render_digest_page()` — idempotent upsert: repeated renders update existing page (stable page ID)
- Slug scheme: `{digest_date}-{section_name_with_dashes}` (e.g. `2026-03-24-companies-business`); deterministic, collision-safe
- `GET /digest-pages/` — list all pages (metadata only, no html_content)
- `GET /digest-pages/{slug}` — returns rendered HTML with `Content-Type: text/html`
- `POST /admin/digests/{digest_run_id}/render` — trigger rendering for a run

**Phase 4C — Ops/admin UI + YAML config** ✅
- YAML config loader with structured sections (app, database, llm, telegram); YAML-only, no env var overrides
- Config path: `config/settings.yaml` (default) or `APP_CONFIG_PATH` env var; git-ignored; `config/settings.example.yaml` is the committed template
- Internal ops UI under `/ui/` — Jinja2 templates, no JS, no SPA
  - Dashboard: pipeline object counts + recent source errors
  - Sources: table with Ingest + Normalize buttons
  - Clusters: table with assessment status, final score + Assess button
  - Digests: table with status, entry counts + Assemble form + Render button + page link
  - Config: read-only config view with secrets masked

**Phase 4D — Telegram publishing** ✅
- `digest_publications` table — one row per `(digest_page_id, channel_type, target)`; idempotent upsert on re-publish
- `app/publishing/telegram.py` — narrow HTTP boundary: `build_message_text()` + `send_telegram_message()` (fully mockable)
- `app/publishing/service.py` — `publish_to_telegram()`: idempotent, reads YAML config, builds public URL, persists result
- Public URL scheme: `{app.public_base_url}/digest-pages/{slug}`
- Message: title, date, section name, public URL (plain text)
- `POST /admin/digest-pages/{id}/publish-telegram` — returns `DigestPublication` JSON
- `GET /digest-publications/`, `GET /digest-publications/{id}`
- UI: Publish to Telegram button on digests page (shown when `telegram.enabled=true`); publication status column

**companies_business relevance gate** ✅
- `should_include_in_companies_business()` — intentionally strict; covers only genuine cybersecurity business news (funding, M&A, earnings, market moves of security vendors)
- Three-layer check: (1) business event-type allowlist → (2) content security signal (keyword or known vendor in title/summary/company — **source name alone is not sufficient**) → (3) generic consumer/tech noise denylist (WhatsApp, ChatGPT, generative AI, Meta AI, consumer streaming/mobility, etc.)
- `_has_content_security_signal()` — internal check that excludes source name; used by the main gate to enforce story-level relevance
- `cluster_passes_companies_business_gate(db, cluster)` — DB-aware wrapper; applied before expensive LLM stages (assess + digest-writer)
- Incidents and regulation will be handled as separate sections; this filter does not cover them

**Phase 4E — Daily scheduler + run orchestration** ✅ *(current)*
- `pipeline_runs` table — one row per pipeline execution; columns: run_date, trigger_type, status, started_at, finished_at, error_message
- `pipeline_run_steps` table — one row per step per run; columns: step_name, status, started_at, finished_at, error_message, details_json
- `run_daily_pipeline()` — sequential orchestrator: ingest all sources → normalize → extract facts → cluster → assess → assemble digest → render → publish Telegram
- Rerun policy: always create a new `pipeline_run` row; stage-level idempotency handles data deduplication
- Failure policy: a hard exception marks the step and run "failed" and stops execution; soft misses (no data) are "success"
- `POST /admin/pipeline-runs/run-daily` — manual trigger with `run_date` + optional `publish_telegram` override
- `GET /pipeline-runs/`, `GET /pipeline-runs/{id}` (with per-step detail)
- APScheduler `BackgroundScheduler` embedded in FastAPI lifespan — `max_instances=1`, `misfire_grace_time=3600`
- YAML `scheduler` section: `enabled`, `daily_time_utc` (HH:MM UTC), `publish_telegram_by_default`
- UI: Pipeline Runs page with step detail table, Run Daily form, status badges

**Not yet implemented:** multi-section orchestration, fuzzy/semantic clustering, config editing via UI.

---

## Local development

### Prerequisites

Docker and Docker Compose.

### Config file (optional)

Runtime configuration is **YAML-only**. No runtime values are read from environment variables. The only accepted env var is `APP_CONFIG_PATH`, which selects which file to load.

```bash
cp config/settings.example.yaml config/settings.yaml
# Edit config/settings.yaml — set database.url, llm.api_key, telegram settings, etc.
# config/settings.yaml is git-ignored and must never be committed.
```

**Committed config files (no secrets, safe to commit):**
- `config/settings.example.yaml` — template; uses `localhost:5432`
- `config/settings.compose.yaml` — Docker Compose defaults; uses `db:5432` service name

**Docker Compose** automatically loads `config/settings.compose.yaml` via `APP_CONFIG_PATH`.
To add credentials (LLM key, Telegram), override: `APP_CONFIG_PATH=/app/config/settings.yaml` and mount your real file.

**CI** uses `APP_CONFIG_PATH=config/settings.example.yaml` (localhost DB matches CI postgres service).

### Start the stack

```bash
docker compose up --build
```

App: http://localhost:8000
API docs: http://localhost:8000/docs
Ops UI: http://localhost:8000/ui/

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
cp config/settings.example.yaml config/settings.yaml
# Edit config/settings.yaml — set database.url, llm.api_key, etc.
export APP_CONFIG_PATH=config/settings.yaml
pip install -r requirements-dev.txt
alembic upgrade head
pytest -v
uvicorn app.main:app --reload
```

---

## Production deployment

### How it works

Every push to `main` that passes CI automatically triggers the `Deploy` workflow, which:

1. Resolves the exact commit SHA being deployed
2. Builds the app Docker image with `--build-arg GIT_SHA=<sha>` and pushes two tags to GHCR: `:latest` and `:<commit-sha>`
3. Generates a deploy-time `compose.prod.yaml` with the exact SHA tag substituted in (not `:latest`)
4. Copies the generated compose file to the server
5. Authenticates the server to GHCR using `DEPLOY_GHCR_TOKEN` (dedicated PAT — does not expire with the workflow)
6. Starts the DB (if not running), waits for it to be healthy
7. Pulls the exact SHA-tagged image — explicit, not relying on local cache
8. Runs `alembic upgrade head` (migrations — idempotent and additive)
9. Runs `docker compose up -d --force-recreate` — unconditionally recreates the app container
10. Verifies `GET /health` responds successfully
11. **Verifies `GET /version` returns the expected SHA** — workflow fails if there is a mismatch

### Version verification

The running app exposes its build SHA at `GET /version`:
```json
{"git_sha": "a1b2c3d4e5f6..."}
```

The deploy workflow queries this endpoint after startup and compares it to the expected SHA. If they do not match, the workflow fails — there is no silent success.

You can also check the running version at any time:
- `GET /version` — JSON API
- `/ui/config` — shows git SHA in the Build section

### Required GitHub repository secrets

| Secret | Purpose |
|--------|---------|
| `DEPLOY_SSH_KEY` | Private SSH key; public key must be in server's `authorized_keys` |
| `DEPLOY_HOST` | Server hostname or IP |
| `DEPLOY_USER` | SSH user on the server |
| `DEPLOY_GHCR_USERNAME` | GitHub username with `read:packages` access to pull images |
| `DEPLOY_GHCR_TOKEN` | GitHub PAT with `read:packages` scope — used for server-side GHCR pull (does not expire with workflow) |

### Server layout

```
/opt/security-digest/
  compose.prod.yaml          — generated and written by deploy workflow on every deploy
  config/
    settings.yaml            — runtime config; NOT in repo; set up once on the server
```

`config/settings.yaml` is mounted read-only into the app container at `/app/config/settings.yaml`.

### Server prerequisites (one-time setup)

1. Install Docker and Docker Compose on the server
2. Create the config directory and settings file:
   ```bash
   mkdir -p /opt/security-digest/config
   cp config/settings.compose.yaml /opt/security-digest/config/settings.yaml
   # Edit /opt/security-digest/config/settings.yaml:
   #   - Set llm.api_key (Anthropic key)
   #   - Set telegram.bot_token and telegram.chat_id if using Telegram
   #   - Set app.public_base_url to your server's public URL
   #   - Leave database.url as postgresql://digest:digest@db:5432/digest
   ```
3. Add the deploy SSH key to `~/.ssh/authorized_keys`
4. Add the five GitHub repository secrets listed above (`DEPLOY_SSH_KEY`, `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_GHCR_USERNAME`, `DEPLOY_GHCR_TOKEN`)

### Compose files: local dev vs production

| | `docker-compose.yml` | `compose.prod.yaml` |
|---|---|---|
| Purpose | Local development | Production server |
| Image | Built from local `Dockerfile` | Pre-built from GHCR (exact SHA tag) |
| App command | `uvicorn --reload` | `uvicorn` (no reload) |
| Code mount | Yes (full repo bind-mount) | No |
| Config | `config/settings.compose.yaml` (in repo) | `/opt/security-digest/config/settings.yaml` (on server) |
| DB port | `5432:5432` exposed to host | Not exposed (internal only) |
| Managed by | Developer | Deploy workflow (generated per deploy) |

### Rollback

Each merged commit SHA has a corresponding immutable GHCR image tag. To roll back:

```bash
# Option 1: trigger the deploy workflow manually via GitHub UI,
# setting the ref to the commit SHA you want to redeploy.

# Option 2: manual rollback on the server
OLD_SHA=<previous-commit-sha>
IMAGE=ghcr.io/tolstuun/digest:$OLD_SHA
docker login ghcr.io -u <DEPLOY_GHCR_USERNAME> -p <DEPLOY_GHCR_TOKEN>
sed "s|ghcr.io/tolstuun/digest:latest|$IMAGE|g" compose.prod.yaml > /tmp/rollback.yaml
docker compose -f /tmp/rollback.yaml pull
docker compose -f /tmp/rollback.yaml up -d --force-recreate
```

GHCR retains tagged images for each merged commit SHA.

---

## Pipeline walkthrough (current)

```
1.  Create a source:      POST /sources/
2.  Ingest:               POST /admin/sources/{id}/ingest
3.  Normalize:            POST /admin/sources/{id}/normalize
4.  Extract facts:        POST /admin/stories/{id}/extract-facts
5.  Cluster:              POST /admin/stories/{id}/cluster-event
6.  Assess:               POST /admin/event-clusters/{id}/assess
7.  Assemble digest:      POST /admin/digests/assemble
8.  Render HTML:          POST /admin/digests/{id}/render
9.  Read page:            GET /digest-pages/{slug}
10. Publish to Telegram:  POST /admin/digest-pages/{id}/publish-telegram
11. Run daily pipeline:   POST /admin/pipeline-runs/run-daily
```

### Triggering the full daily pipeline

```bash
# Trigger the full daily pipeline for a specific date
curl -X POST http://localhost:8000/admin/pipeline-runs/run-daily \
  -H "Content-Type: application/json" \
  -d '{"run_date": "2026-03-25", "publish_telegram": false}'

# View all pipeline runs
curl http://localhost:8000/pipeline-runs/

# View a specific run with step detail
curl http://localhost:8000/pipeline-runs/{pipeline_run_id}
```

### Triggering digest assembly, rendering, and publishing manually

```bash
# Assemble digest for a specific date (companies_business section)
curl -X POST http://localhost:8000/admin/digests/assemble \
  -H "Content-Type: application/json" \
  -d '{"digest_date": "2026-03-24"}'

# Render HTML for the assembled run
curl -X POST http://localhost:8000/admin/digests/{digest_run_id}/render

# Read the rendered HTML page
curl http://localhost:8000/digest-pages/2026-03-24-companies-business

# Publish the page to Telegram (requires telegram.enabled=true in config)
curl -X POST http://localhost:8000/admin/digest-pages/{digest_page_id}/publish-telegram

# List all publications
curl http://localhost:8000/digest-publications/
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
| GET    | /digest-pages/                     | List all rendered digest pages           |
| GET    | /digest-pages/{slug}               | Get rendered HTML page by slug           |
| POST   | /admin/digests/{id}/render                      | Render HTML page for a digest run        |
| POST   | /admin/digest-pages/{id}/publish-telegram       | Publish digest page to Telegram          |
| GET    | /digest-publications/                           | List all digest publications             |
| GET    | /digest-publications/{id}                       | Get digest publication by ID             |
| GET    | /pipeline-runs/                                 | List all pipeline runs                   |
| GET    | /pipeline-runs/{id}                             | Get pipeline run detail with steps       |
| POST   | /admin/pipeline-runs/run-daily                  | Trigger full daily pipeline              |
| GET    | /ui/                                            | Ops UI — dashboard                       |
| GET    | /ui/sources                                     | Ops UI — sources list + action buttons   |
| GET    | /ui/event-clusters                              | Ops UI — clusters list + assess button   |
| GET    | /ui/digests                                     | Ops UI — digest runs + assemble/render/publish |
| GET    | /ui/pipeline-runs                               | Ops UI — pipeline runs + step detail     |
| GET    | /ui/config                                      | Ops UI — read-only config view           |

Full interactive docs: http://localhost:8000/docs

---

## Project structure

```
app/
  config.py               YAML config loader (app/database/llm/telegram sections; YAML-only)
  database.py             SQLAlchemy engine, session, Base
  main.py                 FastAPI app, router registration
  models/
    source.py             Source ORM model
    raw_item.py           RawItem ORM model
    story.py              Story ORM model
    story_facts.py        StoryFacts ORM model
    event_cluster.py      EventCluster ORM model
    event_cluster_assessment.py  EventClusterAssessment ORM model
    digest_run.py         DigestRun ORM model
    digest_entry.py       DigestEntry ORM model
    digest_page.py        DigestPage ORM model
    digest_publication.py DigestPublication ORM model
    pipeline_run.py       PipelineRun ORM model
    pipeline_run_step.py  PipelineRunStep ORM model
  schemas/
    source.py             SourceCreate / SourcePatch / SourceOut
    story.py              StoryOut
    story_facts.py        StoryFactsOut
    digest.py             DigestRunOut / DigestRunDetail / DigestEntryOut
    digest_page.py        DigestPageOut
    digest_publication.py DigestPublicationOut
    pipeline_run.py       PipelineRunOut / PipelineRunDetail / PipelineRunStepOut
  routers/
    health.py             GET /health
    sources.py            GET|POST /sources/, GET|PATCH /sources/{id}
    stories.py            GET /stories/, GET /stories/{id}
    event_clusters.py     GET /event-clusters/, GET /event-clusters/{id}|assessment
    digests.py            GET /digests/, GET /digests/{id}
    digest_pages.py       GET /digest-pages/, GET /digest-pages/{slug}
    digest_publications.py GET /digest-publications/, GET /digest-publications/{id}
    pipeline_runs.py      GET /pipeline-runs/, GET /pipeline-runs/{id}
    admin.py              POST /admin/sources/{id}/ingest|normalize, stories/{id}/extract-facts|cluster-event
                          POST /admin/event-clusters/{id}/assess, /admin/digests/assemble
                          POST /admin/digests/{id}/render, /admin/digest-pages/{id}/publish-telegram
                          POST /admin/pipeline-runs/run-daily
    ui.py                 GET|POST /ui/* — server-rendered ops HTML UI
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
  rendering/
    html.py               render_digest_html() — pure function, no DB, no LLM
    service.py            render_digest_page() — DB upsert of DigestPage
  publishing/
    telegram.py           build_message_text() + send_telegram_message() — narrow HTTP boundary
    service.py            publish_to_telegram() — idempotent upsert, YAML config, public URL
  orchestration/
    service.py            run_daily_pipeline() — sequential 8-step orchestrator; PipelineRun + PipelineRunStep persistence
  scheduler.py            APScheduler wrapper; start_scheduler() / stop_scheduler(); wired into FastAPI lifespan
  templates/
    base.html             base layout with nav bar
    ui/                   ops UI page templates (dashboard, sources, clusters, digests, pipeline-runs, config)
config/
  settings.example.yaml  committed template (copy to settings.yaml for local use)
  settings.compose.yaml  committed Docker Compose config (db hostname, no secrets)
docker-compose.yml        local development compose (build from source, --reload, repo bind-mount)
compose.prod.yaml         production compose (pre-built GHCR image, config mount, no code bind)
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
    0009_add_digest_pages.py
    0010_add_digest_publications.py
    0011_add_pipeline_runs.py
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
  test_rendering.py
  test_publishing.py
  test_orchestration.py
```

---

## See also

- [Roadmap](docs/roadmap.md)
- [Architecture](docs/architecture.md)
