# PeeringDB Local Database Setup

This application uses a local SQLite cache of the PeeringDB database to eliminate rate limiting and improve performance.

## Benefits

- **No Rate Limiting**: All queries are local, no API rate limits
- **Fast Queries**: <10ms response time (vs 200-2000ms for API calls)
- **Reduced Network Dependency**: Only sync once per day
- **Lower Load on PeeringDB**: Fewer API requests to PeeringDB infrastructure

## Setup Instructions

### 1. Set the API Key

The application needs your PeeringDB API key to sync the database.

#### For Docker Deployment

Add the API key to your docker-compose.yml or pass it as an environment variable:

```yaml
services:
  bgp-audit:
    environment:
      - PEERINGDB_API_KEY=XKpRtJT5.9d6rUNr9BuOFpGJitkorpTcli66itvg2
```

Or when running the container:

```bash
docker run -e PEERINGDB_API_KEY=XKpRtJT5.9d6rUNr9BuOFpGJitkorpTcli66itvg2 ...
```

#### For Local Development

```bash
export PEERINGDB_API_KEY=XKpRtJT5.9d6rUNr9BuOFpGJitkorpTcli66itvg2
export PEERINGDB_DB_PATH=/app/data/peeringdb.sqlite3
python main.py
```

### 2. Initial Sync

On first run, the application will automatically perform an initial sync. This downloads the entire PeeringDB database (~50-100MB) and may take 2-5 minutes.

You'll see output like:
```
[PeeringDB] Performing initial sync (this may take a few minutes)...
[PeeringDB] Initial sync complete
```

### 3. Set Up Daily Sync (Production Only)

For production deployments, set up a daily cron job to keep the database updated.

#### Option A: Docker Cron Job (Recommended)

Add this to your crontab on the Docker host:

```bash
# Sync PeeringDB database daily at 3 AM
0 3 * * * docker exec bgp-audit python /app/sync_peeringdb.py >> /var/log/peeringdb-sync.log 2>&1
```

#### Option B: System Cron Job

If running without Docker:

```bash
# Sync PeeringDB database daily at 3 AM
0 3 * * * cd /path/to/bgp-audit && python sync_peeringdb.py >> /var/log/peeringdb-sync.log 2>&1
```

### 4. Persist the Database (Docker)

Make sure the database directory is mounted as a volume so it persists across container restarts:

```yaml
services:
  bgp-audit:
    volumes:
      - ./data:/app/data
```

Or when running:

```bash
docker run -v $(pwd)/data:/app/data ...
```

## Database Information

- **Location**: `/app/data/peeringdb.sqlite3` (configurable via `PEERINGDB_DB_PATH`)
- **Size**: ~50-100MB initial, <1MB daily updates
- **Sync Frequency**: Daily (recommended)
- **Auto-Sync**: Automatically syncs if database is >1 day old on startup

## Monitoring

Check sync logs:

```bash
# View last sync
tail -f /var/log/peeringdb-sync.log

# Check database age
docker exec bgp-audit ls -lh /app/data/peeringdb.sqlite3
```

Check application logs for PeeringDB queries:

```bash
docker logs bgp-audit | grep PeeringDB
```

You should see fast local queries like:
```
[PeeringDB] Local query 'net?asn__in=21859,4229': 2 results
```

## Troubleshooting

### Database Not Updating

Check that:
1. API key is set correctly: `docker exec bgp-audit printenv | grep PEERINGDB`
2. Cron job is running: `docker exec bgp-audit python /app/sync_peeringdb.py`
3. Database file is writable: `docker exec bgp-audit ls -la /app/data/`

### Sync Failures

Check the sync log:
```bash
tail -n 50 /var/log/peeringdb-sync.log
```

Common issues:
- Invalid API key
- Network connectivity problems
- Disk space issues

### Manual Sync

Force a manual sync:
```bash
docker exec bgp-audit python /app/sync_peeringdb.py
```

## Migration from API to Local Database

When you deploy this update:

1. The old API cache files (`.api_cache/`) are no longer used
2. First startup will take 2-5 minutes for initial sync
3. Subsequent startups are instant (<1 second)
4. No rate limiting errors (429) will occur
5. All queries are now <10ms instead of 200-2000ms

You can safely delete the old cache directory after confirming the local database works.
