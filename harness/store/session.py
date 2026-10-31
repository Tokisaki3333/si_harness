"""会话：消息追加、状态快照、回溯。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .messages import MessageStore
from ..models import Turn


class Session:
    def __init__(self, root: str | Path, session_id: str, bucket_size: int = 500):
        self.root = Path(root)
        self.session_id = session_id
        self.messages = MessageStore(root, session_id, bucket_size)

    def append_message(self, role: str, content: str, *, tier: int = 0,
                       turn: int | None = None, branch: str = "main") -> int:
        return self.messages.append(role, content, tier=tier, turn=turn, branch=branch)

    def append_turn(self, user: str, assistant: str, *, turn: int | None = None,
                    tier: int = 0, tools: list[str] | tuple[str, ...] = ()) -> tuple[int, int]:
        u = self.append_message("user", user, tier=tier, turn=turn)
        for block in tools or ():
            self.append_message("tool", block, tier=tier, turn=turn)
        a = self.append_message("assistant", assistant, tier=tier, turn=turn)
        return u, a

    def snapshot(self, seq: int, state: dict) -> None:
        meta = self.messages._meta
        with meta:
            meta.execute(
                "INSERT INTO state_snapshots(seq,ts,payload) VALUES(?,?,?) "
                "ON CONFLICT(seq) DO UPDATE SET ts=excluded.ts, payload=excluded.payload",
                (seq, int(time.time()), json.dumps(state, ensure_ascii=False)))

    def latest_snapshot(self, seq: int | None = None) -> dict | None:
        meta = self.messages._meta
        if seq is None:
            row = meta.execute(
                "SELECT * FROM state_snapshots ORDER BY seq DESC LIMIT 1").fetchone()
        else:
            row = meta.execute(
                "SELECT * FROM state_snapshots WHERE seq<=? ORDER BY seq DESC LIMIT 1",
                (seq,)).fetchone()
        if not row:
            return None
        return {"seq": row["seq"], "state": json.loads(row["payload"])}

    def rewind(self, seq: int) -> dict | None:
        """回到 seq 之前的状态：截断消息与快照，返回最近快照。"""
        self.messages.truncate_after(seq)
        meta = self.messages._meta
        with meta:
            meta.execute("DELETE FROM state_snapshots WHERE seq > ?", (seq,))
        return self.latest_snapshot()

    def append_summary(self, turn: int, text: str, branch: str = "main") -> int:
        """把摘要写到该轮 assistant 原文行的 summary 列。"""
        from .db import connect
        for p in self.messages.buckets():
            conn = connect(p)
            try:
                with conn:
                    cur = conn.execute(
                        "UPDATE messages SET summary=? "
                        "WHERE turn=? AND role='assistant' AND tier=0 AND branch=?",
                        (text, turn, branch))
                    if cur.rowcount:
                        return cur.rowcount
            finally:
                conn.close()
        return 0

    def summaries(self, limit: int | None = None) -> list[dict]:
        from .db import connect
        rows: list[dict] = []
        for p in self.messages.buckets():
            conn = connect(p)
            try:
                rows.extend(dict(r) for r in conn.execute(
                    "SELECT turn, summary FROM messages "
                    "WHERE summary!='' ORDER BY seq"))
            finally:
                conn.close()
        return rows[-limit:] if limit else rows

    def turn_set(self) -> set[int]:
        """当前仍存在的轮次集合（含已压缩轮）。"""
        return {int(r["turn"]) for r in self.messages.by_tier(0)
                if r["turn"] is not None}

    def reload_window(self, turns: int | None = None) -> list[Turn]:
        """按 turn 聚合 user / tool / assistant；已压缩轮次跳过。"""
        rows = sorted(self.messages.by_tier(0), key=lambda r: r["seq"])
        groups: dict[int, list[dict]] = {}
        for r in rows:
            groups.setdefault(int(r["turn"] or 0), []).append(r)
        out: list[Turn] = []
        for turn in sorted(groups):
            user, assistant, summary = "", "", ""
            tools: list[str] = []
            for r in groups[turn]:
                if r["role"] == "user":
                    user = r["content"]
                elif r["role"] == "assistant":
                    assistant = r["content"]
                    summary = r.get("summary") or ""
                elif r["role"] == "tool":
                    tools.append(r["content"])
            if summary:
                continue
            if user or assistant:
                out.append(Turn(user, assistant, index=turn, tools=tools))
        if turns:
            out = out[-turns:]
        for i, t in enumerate(out, 1):
            t.display = i
        return out

    def recent(self, n: int) -> list[dict]:
        return self.messages.last(n)

    def close(self) -> None:
        self.messages.close()
