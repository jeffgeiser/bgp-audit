import sqlite3
import os
from datetime import datetime, timezone
from typing import Optional
from . import config


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.STAGING_DB), exist_ok=True)
    conn = sqlite3.connect(config.STAGING_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            source TEXT NOT NULL,
            department TEXT NOT NULL DEFAULT 'General',
            status TEXT NOT NULL DEFAULT 'Pending',
            auditor TEXT,
            auditor_notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_chunks_status ON chunks(status)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_chunks_department ON chunks(department)
    """)
    conn.commit()
    conn.close()


def insert_chunk(content: str, source: str, department: str = "General") -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO chunks (content, source, department, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'Pending', ?, ?)",
        (content, source, department, now, now),
    )
    chunk_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return chunk_id


def get_chunks(
    status: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    conn = get_connection()
    query = "SELECT * FROM chunks WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if department:
        query += " AND department = ?"
        params.append(department)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_chunk(chunk_id: int) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_chunk(chunk_id: int, **kwargs) -> bool:
    allowed = {"content", "department", "status", "auditor", "auditor_notes"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [chunk_id]
    conn = get_connection()
    conn.execute(f"UPDATE chunks SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def get_stats() -> dict:
    conn = get_connection()
    rows = conn.execute(
        "SELECT status, COUNT(*) as count FROM chunks GROUP BY status"
    ).fetchall()
    conn.close()
    return {r["status"]: r["count"] for r in rows}


def get_department_stats() -> dict:
    conn = get_connection()
    rows = conn.execute(
        "SELECT department, status, COUNT(*) as count "
        "FROM chunks GROUP BY department, status"
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        dept = r["department"]
        if dept not in result:
            result[dept] = {}
        result[dept][r["status"]] = r["count"]
    return result
