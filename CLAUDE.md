# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

This repository is for a modular daily cybersecurity digest platform.

The first implemented section is business and company news in cybersecurity:
- vendor revenue and earnings
- M&A
- funding rounds
- new companies / category entrants
- major market moves
- selected top reads from relevant business and security publications

Later, additional sections will be added:
- major incidents
- conferences
- regulation
- vendor launches
- other curated sections

The system publishes a daily web page and sends a Telegram message with a link to that page.

---

## Current repository state

Phase 4A (digest assembly foundation) is complete. The app runs on FastAPI + PostgreSQL with Alembic migrations, Docker Compose, and GitHub Actions CI.

Current tables: `sources`, `raw_items`, `stories`, `story_facts`, `event_clusters`, `event_cluster_assessments`, `digest_runs`, `digest_entries`.
Current API: `GET /health`, `GET|POST /sources/`, `GET|PATCH /sources/{id}`, `GET /stories/`, `GET /stories/{id}`, `GET /stories/{id}/facts`, `GET /event-clusters/`, `GET /event-clusters/{id}`, `GET /event-clusters/{id}/assessment`, `GET /digests/`, `GET /digests/{id}`, `POST /admin/sources/{id}/ingest`, `POST /admin/sources/{id}/normalize`, `POST /admin/stories/{id}/extract-facts`, `POST /admin/stories/{id}/cluster-event`, `POST /admin/event-clusters/{id}/assess`, `POST /admin/digests/assemble`.

Two LLM boundaries (both mockable):
- `app.extraction.llm.extract_facts_llm(story_input)` — fact extraction
- `app.scoring.llm.assess_cluster_llm(cluster_input)` — editorial scoring

Clustering is deterministic, no LLM. The cluster key is built from `event_type + sorted(company_names) + amount_text + currency`.

Scoring: `rule_score` from `app.scoring.rules.compute_rule_score()` (explicit weights in code) + `llm_score` from LLM → `final_score = 0.4 * rule_score + 0.6 * llm_score`.

Deploy uses SSH secrets: `DEPLOY_SSH_KEY`, `DEPLOY_HOST`, `DEPLOY_USER`. The server path is `/opt/security-digest/`.

---

## Commands

```bash
# Local dev (Docker)
docker compose up --build              # start app + postgres
docker compose run --rm app alembic upgrade head    # apply migrations
docker compose run --rm app pytest -v               # run all tests
docker compose run --rm app pytest tests/test_ingestion.py::test_ingest_avoids_duplicates -v

# Without Docker (requires local PostgreSQL, DATABASE_URL set)
pip install -r requirements-dev.txt
alembic upgrade head
pytest -v
uvicorn app.main:app --reload
```

---

## Product and architecture direction

Build this as a **modular monolith with worker-based pipeline**, not as a distributed microservice system.

Use:
- one repository
- one primary PostgreSQL database
- separate logical workers / entrypoints for pipeline stages
- Docker / Docker Compose for local and server runtime
- GitHub Actions for CI/CD

Do **not** introduce Kubernetes, Kafka, multiple repos, or heavy distributed infrastructure unless explicitly requested.

Design for future split into multiple services, but keep the implementation simple now.

---

## Core architectural principles

1. **Atomic pipeline stages**
   Each stage must be independently runnable and retryable.

2. **Deterministic first, LLM second**
   Use normal code for ingestion, parsing, normalization, deduplication scaffolding, status tracking, and rule-based filtering.
   Use LLM only where language understanding/editorial judgment is needed.

3. **Sources are data, not code**
   Sources must be stored in the database, not hardcoded in Python.

4. **Digest is a set of sections**
   Do not design the system as a single flat news feed.

5. **One story can belong to multiple sections**
   Avoid architecture that assumes one item -> one section.

6. **Store raw inputs**
   Preserve raw source payloads whenever practical so failures can be debugged and processing can be rerun.

7. **Everything should be easy to rerun**
   Recompute enrichment/ranking without needing to re-ingest the same source.

---

## Expected domain model direction

The exact schema may evolve, but implementation should align with this shape:

- `sources`
- `raw_items`
- `stories`
- `story_clusters`
- `entities`
- `sections`
- `digest_runs`
- `digest_entries`
- `job_runs`

### Source model expectations
A source should support fields like:
- name
- type (`rss`, `api`, `html`, `manual`, `newsletter`)
- enabled flag
- section scope / allowed sections
- tags
- priority
- parser type
- polling config
- language
- geography
- notes

---

## LLM usage policy

Use LLM for:
- language-aware classification
- extracting structured facts from messy articles
- canonical short summaries
- rewriting digest text in output language
- editorial scoring / prioritization
- section relevance assessment

Do **not** use LLM for:
- basic persistence
- CRUD logic
- simple parsing if deterministic parsing is enough
- schema decisions that should be explicit in code
- hidden business logic that cannot be tested

Prefer a staged approach:
1. raw ingest
2. normalize
3. cluster/deduplicate
4. enrich
5. score
6. assign to sections
7. render digest

For multilingual sources, do not directly do “freeform pretty translation” from source to final digest text.
Prefer:
1. extract facts into a canonical structured representation
2. generate digest text from that representation in the output language

---

## Development workflow

Always work in a feature branch.
Never commit directly to `main` unless explicitly instructed.

Preferred flow:
1. understand the requested change
2. write or update tests first
3. implement the smallest possible change
4. run the relevant tests locally
5. run lint/format if configured
6. update docs if behavior/architecture changed
7. commit with a clear message
8. push the branch
9. open or update a PR

Prefer small, focused PRs.
Do not mix unrelated refactors into feature work.

---

## Testing rules

Testing-first is the default.

For every non-trivial change:
- add or update tests before or with the implementation
- run only the relevant subset first for fast feedback
- then run the broader test set affected by the change

Test types to prefer:
- unit tests for pure logic
- integration tests for DB-backed flows
- API tests for endpoints
- worker/pipeline tests for stage transitions
- regression tests for previously fixed bugs

Avoid shipping behavior that is not covered by tests unless explicitly instructed.

If a change is hard to test, simplify the design until it becomes testable.

---

## Definition of done

A task is not done until all of the following are true when applicable:
- code implemented
- tests added/updated
- relevant tests pass
- docs updated
- architecture notes/diagram updated if the change affects architecture
- migrations added if schema changed
- local run path still works
- PR is ready

---

## Documentation requirements

Whenever applicable, update:
- `README.md`
- developer setup docs
- API docs or endpoint docs
- architecture notes / ADRs
- architecture diagram / scheme if the change affects system structure

Do not leave architecture drift undocumented.

---

## Coding style expectations

- Prefer simple, explicit code.
- Avoid clever abstractions early.
- Prefer boring, testable solutions.
- Keep functions focused.
- Keep boundaries between modules clear.
- Use typed code where practical.
- Add logging around worker and pipeline boundaries.
- Make retries and failure states visible.
- Use idempotent operations where possible.

Do not perform drive-by refactors unless they are necessary for the requested change.

---

## Error handling and observability

Implement production-minded behavior even in early versions:
- meaningful logs
- explicit statuses
- clear error messages
- predictable retries
- job run visibility
- health checks

Avoid silent failures.

---

## CI/CD expectations

This repository is expected to use simple automated CI/CD:
- GitHub Actions for CI
- tests on PRs
- automatic deploy path after successful merge to `main`
- server deploy through Docker Compose
- smoke checks after deploy

When making changes that affect build/test/deploy behavior:
- update workflows carefully
- keep deploy steps reproducible
- do not introduce unnecessary complexity

---

## Current implementation priority

Current goal is to bootstrap the platform, not to finish the entire product at once.

Current priority order:
1. application skeleton
2. database setup
3. source registry
4. health endpoint
5. source CRUD/API
6. ingestion pipeline foundation
7. worker/status model
8. first digest assembly path
9. first real source connector
10. first LLM enrichment path

Prefer shipping a thin vertical slice end-to-end over building many disconnected parts.

---

## Current first milestone

The first milestone should result in a runnable service with:
- FastAPI app
- PostgreSQL integration
- health endpoint
- `sources` model
- DB migration setup
- source CRUD endpoints
- tests for the above
- Dockerfile
- Docker Compose for local development

Keep the first milestone small and solid.

---

## Constraints

Do not:
- add Kubernetes
- add Kafka
- add a vector DB unless explicitly requested
- split into many repos
- add complex async orchestration systems too early
- overengineer plugin frameworks before real need appears

Do:
- keep extension points clear
- keep schema evolvable
- keep source types flexible
- keep sections configurable

---

## When unsure

When unsure, choose the option that is:
- simpler
- easier to test
- easier to rerun
- easier to observe
- easier to migrate later
- less magical

If a task is too large, break it into the smallest end-to-end shippable increment and implement that.

## GitHub automation rules

### Before starting any implementation task

Do this every time, without being asked:

1. Re-read CLAUDE.md from disk.
2. Check out `main`: `git checkout main`
3. Pull latest: `git pull origin main`
4. Create a new feature branch: `git checkout -b <branch-name>`
5. Only then start implementation.

Do not ask the user to run `git checkout`, `git pull`, or branch creation. Do it yourself unless git auth or git state is actually broken.

### After completing a task

1. Never leave finished work only in a branch.
2. Run the relevant tests.
3. Commit the changes.
4. Push the current branch.
5. If there is no open PR for the current branch, create one targeting `main`.
6. Enable auto-merge for that PR with squash merge.
7. Delete the branch after merge.
8. Do not ask the user to click anything in GitHub unless authentication or permissions are broken.

Use GitHub CLI for this flow.

Preferred commands:

```bash
# start of task
git checkout main
git pull origin main
git checkout -b feat/<branch-name>

# end of task
gh pr view >/dev/null 2>&1 || gh pr create --base main --fill
gh pr merge --auto --squash --delete-branch
```
