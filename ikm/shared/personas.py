import json
import os
from datetime import datetime, timezone
from typing import Optional
from . import config


def _get_connection():
    import sqlite3
    os.makedirs(os.path.dirname(config.STAGING_DB), exist_ok=True)
    conn = sqlite3.connect(config.STAGING_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_personas_db():
    conn = _get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS personas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'Auditor',
            departments TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    # Seed with Admin persona if table is empty
    count = conn.execute("SELECT COUNT(*) FROM personas").fetchone()[0]
    if count == 0:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO personas (name, role, departments, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("Admin", "Admin", json.dumps(config.DEPARTMENTS), now, now),
        )
    conn.commit()
    conn.close()


def get_personas() -> list[dict]:
    conn = _get_connection()
    rows = conn.execute("SELECT * FROM personas ORDER BY name").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["departments"] = json.loads(d["departments"])
        result.append(d)
    return result


def get_persona(persona_id: int) -> Optional[dict]:
    conn = _get_connection()
    row = conn.execute("SELECT * FROM personas WHERE id = ?", (persona_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["departments"] = json.loads(d["departments"])
    return d


def create_persona(name: str, role: str, departments: list[str]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_connection()
    cursor = conn.execute(
        "INSERT INTO personas (name, role, departments, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (name, role, json.dumps(departments), now, now),
    )
    pid = cursor.lastrowid
    conn.commit()
    conn.close()
    return pid


def update_persona(persona_id: int, **kwargs):
    conn = _get_connection()
    updates = {}
    if "name" in kwargs:
        updates["name"] = kwargs["name"]
    if "role" in kwargs:
        updates["role"] = kwargs["role"]
    if "departments" in kwargs:
        updates["departments"] = json.dumps(kwargs["departments"])
    if not updates:
        conn.close()
        return
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [persona_id]
    conn.execute(f"UPDATE personas SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def delete_persona(persona_id: int):
    conn = _get_connection()
    conn.execute("DELETE FROM personas WHERE id = ?", (persona_id,))
    conn.commit()
    conn.close()
