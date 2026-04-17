#!/usr/bin/env bash
set -euo pipefail
COMPOSE_FILE="/var/www/dashboards/docker-compose.yml"
SERVICE="bgp-audit"

cd "$(dirname "$0")"
echo "==> Pulling latest..."
git pull origin main

echo "==> Rebuilding $SERVICE..."
docker compose -f "$COMPOSE_FILE" build "$SERVICE"
docker compose -f "$COMPOSE_FILE" up -d --force-recreate "$SERVICE"

echo "==> Done."
docker compose -f "$COMPOSE_FILE" logs "$SERVICE" --tail=20
