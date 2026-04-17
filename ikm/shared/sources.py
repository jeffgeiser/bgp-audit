import os
import json
from datetime import datetime, timezone
from typing import Optional
from . import config


def _get_connection():
    import sqlite3
    os.makedirs(os.path.dirname(config.STAGING_DB), exist_ok=True)
    conn = sqlite3.connect(config.STAGING_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_sources_db():
    conn = _get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url_or_filename TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'url',
            department TEXT NOT NULL DEFAULT 'General',
            ingested_by TEXT NOT NULL,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            last_crawled TEXT,
            status TEXT NOT NULL DEFAULT 'Active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_dept ON sources(department)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_ingested_by ON sources(ingested_by)")
    conn.commit()
    conn.close()


def add_source(
    url_or_filename: str,
    source_type: str,
    department: str,
    ingested_by: str,
    chunk_count: int = 0,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_connection()
    cursor = conn.execute(
        "INSERT INTO sources (url_or_filename, type, department, ingested_by, chunk_count, last_crawled, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'Active', ?, ?)",
        (url_or_filename, source_type, department, ingested_by, chunk_count, now, now, now),
    )
    source_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return source_id


def get_sources(
    department: Optional[str] = None,
    ingested_by: Optional[str] = None,
    status: str = "Active",
) -> list[dict]:
    conn = _get_connection()
    query = "SELECT * FROM sources WHERE 1=1"
    params = []
    if department:
        query += " AND department = ?"
        params.append(department)
    if ingested_by:
        query += " AND ingested_by = ?"
        params.append(ingested_by)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_source(source_id: int) -> Optional[dict]:
    conn = _get_connection()
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_source(source_id: int, **kwargs):
    conn = _get_connection()
    allowed = {"chunk_count", "last_crawled", "status", "department"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        conn.close()
        return
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [source_id]
    conn.execute(f"UPDATE sources SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def delete_source(source_id: int):
    conn = _get_connection()
    conn.execute("UPDATE sources SET status = 'Deleted', updated_at = ? WHERE id = ?",
                 (datetime.now(timezone.utc).isoformat(), source_id))
    conn.commit()
    conn.close()


def get_chunks_by_source(source_name: str) -> list[int]:
    """Get chunk IDs that came from a specific source."""
    conn = _get_connection()
    rows = conn.execute("SELECT id FROM chunks WHERE source = ?", (source_name,)).fetchall()
    conn.close()
    return [r["id"] for r in rows]
