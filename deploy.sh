#!/usr/bin/env bash
set -euo pipefail
SERVICE="bgp-audit"

cd "$(dirname "$0")"
echo "==> Pulling latest..."
git pull origin main

echo "==> Rebuilding $SERVICE..."
docker compose build "$SERVICE"
docker compose up -d --force-recreate "$SERVICE"

echo "==> Done."
docker compose logs "$SERVICE" --tail=20
