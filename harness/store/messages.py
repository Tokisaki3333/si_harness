"""上下文记录：全局序号 + 分桶文件。

每 bucket_size 条一个 SQLite 文件，避免单文件无限增长。
"""
from __future__ import annotations

import time
from pathlib import Path

from .db import connect, init_messages, init_meta


class MessageStore:
    def __init__(self, root: str | Path, session_id: str, bucket_size: int = 500):
        self.root = Path(root)
        self.session_id = session_id
        self.bucket_size = bucket_size
        self.dir = self.root / "sessions" / session_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._meta = connect(self.dir / "meta.db")
        init_meta(self._meta)
        self._conns: dict[int, object] = {}

    # ---- seq ----
    def _next_seq(self) -> int:
        with self._meta:
            row = self._meta.execute("SELECT value FROM meta WHERE key='next_seq'").fetchone()
            seq = int(row["value"]) if row else 1
            self._meta.execute(
                "INSERT INTO meta(key,value) VALUES('next_seq',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(seq + 1),))
        return seq

    def next_seq(self) -> int:
        row = self._meta.execute("SELECT value FROM meta WHERE key='next_seq'").fetchone()
        return int(row["value"]) if row else 1

    # ---- buckets ----
    def bucket_index(self, seq: int) -> int:
        return (seq - 1) // self.bucket_size

    def bucket_path(self, index: int) -> Path:
        return self.dir / f"bucket_{index:04d}.db"

    def _bucket_conn(self, index: int):
        if index not in self._conns:
            conn = connect(self.bucket_path(index))
            init_messages(conn)
            self._conns[index] = conn
        return self._conns[index]

    def buckets(self) -> list[Path]:
        return sorted(self.dir.glob("bucket_*.db"))

    # ---- write / read ----
    def append(self, role: str, content: str, *, tier: int = 0,
               turn: int | None = None, branch: str = "main") -> int:
        content = content.encode("utf-8", "ignore").decode("utf-8")
        seq = self._next_seq()
        conn = self._bucket_conn(self.bucket_index(seq))
        with conn:
            conn.execute(
                "INSERT INTO messages(seq,ts,role,content,tier,turn,branch) "
                "VALUES(?,?,?,?,?,?,?)",
                (seq, int(time.time()), role, content, tier, turn, branch))
        return seq

    def get(self, seq: int) -> dict | None:
        conn = self._bucket_conn(self.bucket_index(seq))
        row = conn.execute("SELECT * FROM messages WHERE seq=?", (seq,)).fetchone()
        return dict(row) if row else None

    def range(self, start: int, end: int) -> list[dict]:
        out: list[dict] = []
        if end < start:
            return out
        for b in range(self.bucket_index(start), self.bucket_index(end) + 1):
            conn = self._bucket_conn(b)
            rows = conn.execute(
                "SELECT * FROM messages WHERE seq BETWEEN ? AND ? ORDER BY seq",
                (start, end)).fetchall()
            out.extend(dict(r) for r in rows)
        return out

    def last(self, n: int) -> list[dict]:
        start = max(1, self.next_seq() - n)
        return self.range(start, self.next_seq() - 1)

    def by_tier(self, tier: int, limit: int | None = None) -> list[dict]:
        rows: list[dict] = []
        for p in self.buckets():
            conn = connect(p)
            try:
                rows.extend(dict(r) for r in conn.execute(
                    "SELECT * FROM messages WHERE tier=? ORDER BY seq", (tier,)))
            finally:
                conn.close()
        return rows[-limit:] if limit else rows

    def max_turn(self) -> int:
        m = 0
        for p in self.buckets():
            conn = connect(p)
            try:
                row = conn.execute("SELECT MAX(turn) AS m FROM messages").fetchone()
                m = max(m, int(row["m"] or 0))
            finally:
                conn.close()
        return m

    def count(self) -> int:
        total = 0
        for p in self.buckets():
            conn = connect(p)
            try:
                row = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()
                total += int(row["c"])
            finally:
                conn.close()
        return total

    def truncate_after(self, seq: int) -> int:
        """回溯：删除 seq 之后的所有记录，返回删除条数。"""
        removed = 0
        for p in self.buckets():
            conn = connect(p)
            try:
                with conn:
                    cur = conn.execute("DELETE FROM messages WHERE seq > ?", (seq,))
                    removed += cur.rowcount or 0
            finally:
                conn.close()
        with self._meta:
            self._meta.execute(
                "INSERT INTO meta(key,value) VALUES('next_seq',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(seq + 1),))
        return removed

    def close(self) -> None:
        for conn in self._conns.values():
            conn.close()
        self._conns.clear()
        self._meta.close()
