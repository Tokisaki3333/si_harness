"""长期记忆：触发点 + 条目，落 SQLite。

`entries` 以 JSON 数组存储，条目正文里出现中文分号也不会被拆开；
读取旧库时兼容早期的「；」拼接格式。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .db import connect, init_memory


def _entries(row: dict) -> list[str]:
    raw = str(row.get("entries") or "")
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [x for x in raw.split("；") if x]
    if isinstance(value, list):
        return [str(x) for x in value if str(x)]
    return [raw]


class MemoryDB:
    def __init__(self, root: str | Path, session_id: str):
        self.session_id = session_id
        self.conn = connect(Path(root) / "memory.db")
        init_memory(self.conn)

    def upsert(self, trigger: str, op: str, body: str, *,
               importance: float = 0.5, turn: int | None = None) -> dict:
        trigger = trigger.encode("utf-8", "ignore").decode("utf-8")
        body = body.encode("utf-8", "ignore").decode("utf-8")
        row = self.conn.execute(
            "SELECT * FROM memory WHERE session_id=? AND trigger=?",
            (self.session_id, trigger)).fetchone()
        now = int(time.time())
        if row is None:
            entries = [body]
            self.conn.execute(
                "INSERT INTO memory(session_id,trigger,summary,entries,importance,turn,invalid,updated_at) "
                "VALUES(?,?,?,?,?,?,0,?)",
                (self.session_id, trigger, body[:20],
                 json.dumps(entries, ensure_ascii=False), importance, turn, now))
        else:
            if op == "replace":
                entries = [body]
            else:
                entries = _entries(dict(row))
                if body not in entries:
                    entries.append(body)
            self.conn.execute(
                "UPDATE memory SET entries=?, summary=?, importance=?, turn=?, invalid=0, updated_at=? "
                "WHERE session_id=? AND trigger=?",
                (json.dumps(entries, ensure_ascii=False),
                 (entries[0] if entries else "")[:20],
                 importance, turn, now, self.session_id, trigger))
        self.conn.commit()
        return self.get(trigger)

    def get(self, trigger: str) -> dict | None:
        trigger = trigger.encode("utf-8", "ignore").decode("utf-8")
        row = self.conn.execute(
            "SELECT * FROM memory WHERE session_id=? AND trigger=?",
            (self.session_id, trigger)).fetchone()
        return dict(row) if row else None

    def get_by_id(self, mem_id: str | int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM memory WHERE session_id=? AND id=?",
            (self.session_id, mem_id)).fetchone()
        return dict(row) if row else None

    def search_by_id(self, mem_id: str | int) -> str:
        row = self.get_by_id(mem_id)
        if not row or row["invalid"]:
            return "未找到相关记忆。"
        return f"{row['trigger']}：" + "；".join(_entries(row))

    def all(self, include_invalid: bool = False) -> list[dict]:
        sql = "SELECT * FROM memory WHERE session_id=?"
        if not include_invalid:
            sql += " AND invalid=0"
        return [dict(r) for r in self.conn.execute(sql, (self.session_id,)).fetchall()]

    def index_line(self) -> str:
        rows = self.all()
        if not rows:
            return ""
        return "【记忆】" + "；".join(f"{r['trigger']}:{r['summary']}" for r in rows)

    def search(self, trigger: str) -> str:
        row = self.get(trigger)
        if not row or row["invalid"]:
            return "未找到相关记忆。"
        return f"{row['trigger']}：" + "；".join(_entries(row))

    def invalidate_after(self, turn: int) -> int:
        cur = self.conn.execute(
            "UPDATE memory SET invalid=1 WHERE session_id=? AND turn > ?",
            (self.session_id, turn))
        self.conn.commit()
        return cur.rowcount or 0

    def close(self) -> None:
        self.conn.close()
