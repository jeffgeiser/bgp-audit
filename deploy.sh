#!/bin/bash
# deploy.sh — Pull latest changes and rebuild/restart the app
# Usage: ./deploy.sh [branch]
#   branch defaults to "main"

set -e

BRANCH="${1:-main}"
APP_DIR="/var/www/dashboards/bgp-audit"

echo "=== ZenBrain Deploy ==="
echo "Branch: $BRANCH"
echo ""

cd "$APP_DIR"

# Pull latest
echo "[1/4] Pulling latest from origin/$BRANCH..."
git pull origin "$BRANCH"

# Stop existing containers
echo "[2/4] Stopping containers..."
docker-compose down

# Rebuild with no cache
echo "[3/4] Rebuilding (no cache)..."
docker-compose build --no-cache

# Start
echo "[4/4] Starting containers..."
docker-compose up -d

echo ""
echo "=== Deploy complete ==="
docker ps --filter "name=bgp_audit" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo ""
echo "Test: curl http://localhost:8001/"
