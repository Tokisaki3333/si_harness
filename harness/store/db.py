"""SQLite 连接与 schema。"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_meta(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS state_snapshots (
        seq INTEGER PRIMARY KEY,
        ts INTEGER NOT NULL,
        payload TEXT NOT NULL
    );
    """)


def init_messages(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS messages (
        seq INTEGER PRIMARY KEY,
        ts INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        tier INTEGER NOT NULL DEFAULT 0,
        turn INTEGER,
        branch TEXT NOT NULL DEFAULT 'main'
    );
    CREATE INDEX IF NOT EXISTS idx_messages_turn ON messages(turn);
    CREATE INDEX IF NOT EXISTS idx_messages_tier ON messages(tier);
    """)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
    if "summary" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN summary TEXT NOT NULL DEFAULT ''")


def init_memory(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        trigger TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        entries TEXT NOT NULL DEFAULT '',
        importance REAL NOT NULL DEFAULT 0.5,
        turn INTEGER,
        invalid INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL,
        UNIQUE(session_id, trigger)
    );
    CREATE INDEX IF NOT EXISTS idx_memory_session ON memory(session_id);
    """)
