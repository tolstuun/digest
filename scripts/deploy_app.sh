#!/usr/bin/env bash
# deploy_app.sh — server-side app deployment script.
#
# Called by the GitHub Actions deploy workflow after:
#   - compose.prod.yaml has been copied to /opt/security-digest/
#   - docker login to GHCR has been performed
#
# Usage:
#   bash /opt/security-digest/deploy_app.sh <exact-image-reference>
#
# Example:
#   bash /opt/security-digest/deploy_app.sh ghcr.io/tolstuun/digest:abc1234...
#
# Rollback: call this script with the SHA of any previously built image.
set -euo pipefail

DEPLOY_IMAGE="${1:?Usage: deploy_app.sh <image>}"
COMPOSE="docker compose -f /opt/security-digest/compose.prod.yaml"

echo "============================================================"
echo "=== deploy_app.sh starting"
echo "=== image: $DEPLOY_IMAGE"
echo "============================================================"

cd /opt/security-digest

# ── 1. Ensure DB is up and healthy ────────────────────────────────────────────
echo "--- [1/6] Starting DB and waiting for healthy state ---"
$COMPOSE up -d db
for i in $(seq 1 12); do
  $COMPOSE exec -T db pg_isready -U digest -d digest && break
  echo "  attempt $i/12 — retrying in 5s"
  sleep 5
done

# ── 2. Pull exact SHA-tagged image ────────────────────────────────────────────
echo "--- [2/6] Pulling image: $DEPLOY_IMAGE ---"
docker pull "$DEPLOY_IMAGE"

# ── 3. Verify image is present locally ───────────────────────────────────────
echo "--- [3/6] Verifying image is present locally ---"
docker image inspect "$DEPLOY_IMAGE" --format "ID: {{.Id}}  Created: {{.Created}}"

# ── 4. Run migrations ─────────────────────────────────────────────────────────
echo "--- [4/6] Running migrations ---"
$COMPOSE run --rm app alembic upgrade head

# ── 5. Replace app container ──────────────────────────────────────────────────
echo "--- [5/6] Replacing app container ---"
$COMPOSE rm -sf app || true
$COMPOSE up -d app

# ── 6. Log running container image ───────────────────────────────────────────
echo "--- [6/6] Verifying running container ---"
CONTAINER_ID=$($COMPOSE ps -q app)
docker inspect --format "Running image: {{.Config.Image}}" "$CONTAINER_ID"

echo "============================================================"
echo "=== deploy_app.sh completed successfully"
echo "============================================================"
