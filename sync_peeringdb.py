#!/usr/bin/env python3
"""
PeeringDB Local Database Sync Script

This script syncs the local PeeringDB SQLite database with the latest data
from PeeringDB. It should be run daily via cron.

Usage:
    python sync_peeringdb.py

Environment Variables:
    PEERINGDB_API_KEY - Your PeeringDB API key
    PEERINGDB_DB_PATH - Path to SQLite database (default: /app/peeringdb.sqlite3)
"""

import os
import sys
import time
from peeringdb import resource
from peeringdb.client import Client as PeeringDBClient

def main():
    """Sync PeeringDB local database."""
    api_key = os.environ.get("PEERINGDB_API_KEY", "")
    db_path = os.environ.get("PEERINGDB_DB_PATH", "/app/data/peeringdb.sqlite3")

    if not api_key:
        print("ERROR: PEERINGDB_API_KEY environment variable not set")
        sys.exit(1)

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting PeeringDB sync...")
    print(f"Database path: {db_path}")

    try:
        # Configure peeringdb client
        cfg = {
            "sync": {
                "url": "https://www.peeringdb.com/api",
                "user": api_key,
                "password": "",
                "strip_tz": 1,
                "timeout": 0,
            },
            "orm": {
                "database": db_path,
                "backend": "django_peeringdb",
            }
        }

        # Initialize the client
        pdb = PeeringDBClient(cfg=cfg)

        # Check database age before sync
        if os.path.exists(db_path):
            age_seconds = time.time() - os.path.getmtime(db_path)
            age_days = age_seconds / 86400
            print(f"Database age before sync: {age_days:.1f} days")

        # Perform sync
        print("Syncing with PeeringDB...")
        start_time = time.time()
        pdb.update_all()
        duration = time.time() - start_time

        # Report results
        print(f"Sync completed successfully in {duration:.1f} seconds")

        # Get database stats
        if os.path.exists(db_path):
            size_mb = os.path.getsize(db_path) / (1024 * 1024)
            print(f"Database size: {size_mb:.1f} MB")

            # Count records
            try:
                net_count = len(pdb.all(resource.Network))
                fac_count = len(pdb.all(resource.Facility))
                ix_count = len(pdb.all(resource.InternetExchange))

                print("Database stats:")
                print(f"  Networks: {net_count}")
                print(f"  Facilities: {fac_count}")
                print(f"  Internet Exchanges: {ix_count}")
            except Exception as e:
                print(f"  (Could not retrieve stats: {e})")

        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Sync complete")
        sys.exit(0)

    except Exception as e:
        print(f"ERROR: Sync failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
