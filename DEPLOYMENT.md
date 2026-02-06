# Deployment Instructions for PeeringDB Local Database

## Changes Required on Server

### 1. Update docker-compose.yml

Add the following to the `bgp-audit` service in `/var/www/dashboards/docker-compose.yml`:

```yaml
services:
  bgp-audit:
    # ... existing configuration ...
    environment:
      - PEERINGDB_API_KEY=XKpRtJT5.9d6rUNr9BuOFpGJitkorpTcli66itvg2
      - PEERINGDB_DB_PATH=/app/data/peeringdb.sqlite3
    volumes:
      - ./bgp-audit/data:/app/data  # Persist PeeringDB database
```

**Important**: The volume mount ensures the database persists across container restarts and updates.

### 2. Create Data Directory

Before deploying, create the data directory on the server:

```bash
sudo mkdir -p /var/www/dashboards/bgp-audit/data
sudo chown -R zen:zen /var/www/dashboards/bgp-audit/data
```

### 3. Deploy

The GitHub Actions workflow will handle the deployment. On first startup after deployment:

1. Application will initialize PeeringDB local database
2. Initial sync will take 2-5 minutes (downloads ~50-100MB)
3. Subsequent startups will be instant

Monitor the logs:
```bash
docker logs -f bgp-audit
```

You should see:
```
[PeeringDB] Initializing local database...
[PeeringDB] Performing initial sync (this may take a few minutes)...
[PeeringDB] Initial sync complete
[PeeringDB] Database size: 87.3 MB
```

### 4. Set Up Daily Sync (Cron Job)

Add to root crontab:

```bash
sudo crontab -e
```

Add this line:
```
# Sync PeeringDB database daily at 3 AM
0 3 * * * docker exec bgp-audit python /app/sync_peeringdb.py >> /var/log/peeringdb-sync.log 2>&1
```

### 5. Verify

After deployment, verify the local database is working:

```bash
# Check database exists and has data
docker exec bgp-audit ls -lh /app/data/peeringdb.sqlite3

# Check application logs for local queries (not API calls)
docker logs bgp-audit | grep "PeeringDB"

# You should see:
# [PeeringDB] Local query 'net?asn__in=21859,4229': 2 results
# Instead of API errors or slow responses
```

### 6. Test Rate Limiting is Gone

1. Navigate to dashboard: https://your-domain.com/audit
2. Rapidly switch between cities (London, Ashburn, etc.)
3. All data should load instantly without errors
4. No more 429 rate limiting errors in logs

## Expected Results

### Before (API-based)
- Query time: 200-2000ms per request
- Rate limiting errors (429) when switching cities quickly
- Empty results from cached failures
- Network dependency for every query

### After (Local Database)
- Query time: <10ms per request
- No rate limiting errors
- All data loads instantly
- Only network dependency is daily sync

## Rollback Plan

If issues occur, rollback by:

1. Revert to previous Docker image
2. Or comment out `initialize_peeringdb()` call in main.py
3. Old API-based code will work as fallback

## Monitoring

Check database sync health:

```bash
# View sync log
sudo tail -f /var/log/peeringdb-sync.log

# Check database age (should be <1 day)
docker exec bgp-audit stat /app/data/peeringdb.sqlite3

# Manual sync if needed
docker exec bgp-audit python /app/sync_peeringdb.py
```

## API Key Security

The API key is stored as an environment variable in docker-compose.yml. For additional security:

1. Restrict file permissions: `chmod 600 /var/www/dashboards/docker-compose.yml`
2. Or use Docker secrets (for Swarm mode)
3. Or use external secrets management (Vault, etc.)

## Disk Space

- Initial database: ~50-100MB
- Daily updates: <1MB
- Total space needed: ~200MB (with room for growth)

Verify disk space:
```bash
df -h /var/www/dashboards/bgp-audit/data
```
