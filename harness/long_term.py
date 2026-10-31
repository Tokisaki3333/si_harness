"""长期记忆：客观事件索引 + 情感 episode 卡。

归属 `modules/shi_harness/long_term_memory`，但运行时通过本模块接入。
当前阶段使用公用大库：owner_id 默认 public，scope 默认 shared，不做鉴权。
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .affect import AXES, Candidate, select

PROTOCOL = """【长期记忆协议】
每轮回复末尾追加以下协议行，不要解释：
[[USER_STATE v=3 a=3 agency=3 self_focus=3]]
[[EVENT {"type":"commitment","title":"简短标题","text":"发生了什么","people":[]}]]
[[EPISODE {"thread_id":"可选同一话题","anchors":["T1:青岛市+海洋经济+政策"],"v":3,"a":3,"agency":3,"self_focus":3,"summary":"一句摘要","diary":"第一人称日记"}]]
[[MEM trigger=T1:青岛市+海洋经济+政策 op=append|replace body=客观描述或评价]]
[[USED ep_xxxxxxxx, ep_yyyyyyyy]]
上一轮提示锚点缺失时，用 [[ANCHOR ep_xxxxxxxx T1:实体+属性]] 补交，一张卡一行，可逗号分隔多个；补不上写 [[ANCHOR none]]。
没有内容时分别写 [[USER_STATE none]] / [[EVENT none]] / [[EPISODE none]] / [[MEM none]] / [[USED none]]。
EVENT type 只能是 commitment / plan / fact / event / observation / preference / relationship。
EPISODE 是当前角色的主观情感卡：v/a/agency/self_focus 取 1–5；summary ≤40 字；
diary ≤120 字，写“我当时觉得/我在意/我想做什么”。thread_id 相同且同一话题、四维变化 ≤1 才续写；换话题或任一维变化 ≥2 必须新 thread_id。
索引必须标注模板，元素用 + 分隔（T6 用 |）：
T1 实体名词短语：{修饰/限定}+{核心实体}，如 青岛市+海洋经济+政策
T2 属性短语：{实体}+{属性名}，如 小明+头发
T3 关系短语：{主体}+{关系词}+{客体}，如 小明+就读于+清华大学
T4 事件短语：{施事}+{动作}+{受事}，如 张三+批评+李四
T5 时空限定：{时间}+{地点}+{主题}+{类型}，如 2024+青岛+海洋经济+政策
T6 别名规范：{规范名}|{别名}，如 北京大学|北大
T7 复合限定：{时间}+{地点}+{主体}+{核心实体}，如 2024+北京+甲公司+财报
MEM trigger 与 EPISODE.anchors 必须使用同一模板格式；可超过 10 字。
每轮可写 0..N 条 [[MEM]]；一条记忆只放一个客观事实；trigger 是索引，body 是该索引对应的简短客观正文，二者必须匹配；不同事实拆成不同行。
候选标注 [未展开] 且与当前话题相关时，必须调用 memory_read(target=…) 展开正文；不得凭标题或摘要补全。
情感记忆索引与客观记忆索引是两个维度：情感卡用 target=ep_…，客观记忆用 target=mem:id 或 trigger；相关时两边都必须各自检索。
示例：
[[MEM trigger=T1:青岛市+海洋经济+政策 op=append body=青岛市发布海洋经济政策]]
[[MEM trigger=T5:2024+青岛+海洋经济+政策 op=append body=2024年青岛发布海洋经济政策]]
[[EPISODE {"thread_id":"青岛市+海洋经济+政策","anchors":["T1:青岛市+海洋经济+政策","T5:2024+青岛+海洋经济+政策"],"v":2,"a":3,"agency":3,"self_focus":4,"summary":"听到政策落地，我心里一紧","diary":"我当时觉得这事和我有关，想先记下来。"}]]
【记忆候选】只给索引摘要；统一用 memory_read(target=ep_… / mem:id / trigger) 展开正文；两个维度相关时必须各自检索。
"""

# 每轮候选池上限。超过此数后 SQLite 全表读取 + Python 侧 JSON/打分排序会成为
# 主循环的固定开销，需要换检索后端（倒排/向量/分页）再往上扩。
CANDIDATE_POOL = 5000

_DEFAULT_FOLDERS = (
    ("commitments", "约定、承诺、待办", "commitment"),
    ("facts", "稳定事实", "fact"),
    ("events", "发生过的事", "event"),
    ("observations", "观察与推断", "observation"),
    ("people", "人物", "person"),
    ("places", "地点", "place"),
    ("topics", "话题", "topic"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return text.strip()[:limit]


def _apply_alias(text: str, mapping: dict[str, str]) -> str:
    if not text or not mapping:
        return text
    return re.sub(r"[我你]", lambda m: mapping.get(m.group(), m.group()), text)


def _card_tokens(row: dict) -> list[str]:
    """只取 T1..T7 索引的 tag；summary 是自然语言，不参与匹配。"""
    anchors = row.get("anchors") or "[]"
    try:
        anchors = json.loads(anchors) if isinstance(anchors, str) else anchors
    except (json.JSONDecodeError, TypeError):
        anchors = []
    if not isinstance(anchors, list):
        anchors = [anchors]
    tokens: list[str] = []
    for anchor in anchors:
        body = re.sub(r"^T[1-7]:", "", str(anchor))
        tokens += [x for x in re.split(r"[+|]", body) if len(x) >= 2]
    return tokens


ROLE_ALIASES = {
    "user": {"我": "傅雷", "你": "傅聪"},
    "card": {"我": "傅聪", "你": "傅雷"},
}


def _semantic_score(row: dict, user_msg: str,
                    aliases: dict[str, dict[str, str]] | None = ROLE_ALIASES) -> int:
    if not user_msg:
        return 0
    text = _apply_alias(user_msg, aliases.get("user", {})) if aliases else user_msg
    return sum(1 for t in _card_tokens(row) if t in text)


def _axis(value: Any) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 3


def anchor_is_valid(value: Any) -> bool:
    """Objective index: T1..T7 template, elements separated by + (T6 by |)."""
    text = str(value or "").strip()
    if not text or len(text) > 120:
        return False
    match = re.match(r"^T([1-7]):(.+)$", text)
    if not match:
        return False
    kind = int(match.group(1))
    body = match.group(2).strip()
    if kind == 6:
        parts = [x.strip() for x in body.split("|")]
        return len(parts) >= 2 and all(parts)
    parts = [x.strip() for x in body.split("+")]
    if not all(parts):
        return False
    if kind == 1:
        return len(parts) >= 2
    return len(parts) == {2: 2, 3: 3, 4: 3, 5: 4, 7: 4}[kind]


def _id(prefix: str, data: dict) -> str:
    raw = str(data.get("id") or "").strip()
    return raw if raw else f"{prefix}_{uuid.uuid4().hex[:8]}"


class LongTermMemory:
    def __init__(self, root: str | Path, session_id: str = "shared",
                 owner_id: str = "public", scope: str = "shared",
                 allow_merge: bool = False):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self.owner_id = owner_id
        self.scope = scope
        self.last_ids: list[str] = []
        self.last_groups = {"delta": [], "semantic": [], "random": []}
        self.last_scores: dict[str, dict] = {}
        self.last_episode_ids: list[str] = []
        self.allow_merge = allow_merge
        self.conn = sqlite3.connect(str(self.root / "long_term.db"), timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    # ---- schema ----
    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                facets TEXT NOT NULL DEFAULT '{}',
                occurred_at TEXT,
                due_at TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                epistemic TEXT NOT NULL DEFAULT 'observed',
                source_turn INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_owner ON events(owner_id, scope);
            CREATE INDEX IF NOT EXISTS idx_events_turn ON events(source_turn);

            CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active',
                parent_id TEXT,
                v INTEGER NOT NULL,
                a INTEGER NOT NULL,
                agency INTEGER NOT NULL,
                self_focus INTEGER NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                diary TEXT NOT NULL DEFAULT '',
                anchors TEXT NOT NULL DEFAULT '[]',
                source_turn INTEGER,
                created_at TEXT NOT NULL,
                last_used_turn INTEGER,
                used_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_episodes_owner ON episodes(owner_id, scope);
            CREATE INDEX IF NOT EXISTS idx_episodes_turn ON episodes(source_turn);

            CREATE TABLE IF NOT EXISTS folders (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entry_folders (
                entry_id TEXT NOT NULL,
                folder_id TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                PRIMARY KEY (entry_id, folder_id)
            );
            CREATE TABLE IF NOT EXISTS used_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id TEXT NOT NULL,
                turn INTEGER,
                quote TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS episode_anchors (
                episode_id TEXT NOT NULL,
                anchor TEXT NOT NULL,
                objective_trigger TEXT,
                status TEXT NOT NULL DEFAULT 'missing',
                template TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (episode_id, anchor)
            );
            CREATE INDEX IF NOT EXISTS idx_episode_anchors_anchor
                ON episode_anchors(anchor);

            CREATE TABLE IF NOT EXISTS user_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                v INTEGER NOT NULL,
                a INTEGER NOT NULL,
                agency INTEGER NOT NULL,
                self_focus INTEGER NOT NULL,
                source_turn INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_faults (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                episode_id TEXT NOT NULL,
                anchor TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 1,
                turn INTEGER,
                detail TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '',
                available_tags TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(episode_id, anchor, status)
            );
            CREATE INDEX IF NOT EXISTS idx_memory_faults_state
                ON memory_faults(state);
            CREATE INDEX IF NOT EXISTS idx_memory_faults_episode
                ON memory_faults(episode_id);

            CREATE TABLE IF NOT EXISTS memory_index (
                target TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                source_turn INTEGER,
                clarity TEXT NOT NULL DEFAULT 'clear',
                updated_turn INTEGER,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memory_index_clarity
                ON memory_index(clarity);
            """
        )
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(episodes)")}
        if "quality" not in cols:
            self.conn.execute(
                "ALTER TABLE episodes ADD COLUMN quality INTEGER NOT NULL DEFAULT 0")
        if "quality_note" not in cols:
            self.conn.execute(
                "ALTER TABLE episodes ADD COLUMN quality_note TEXT NOT NULL DEFAULT ''")
        anchor_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(episode_anchors)")}
        if "template" not in anchor_cols:
            self.conn.execute("ALTER TABLE episode_anchors ADD COLUMN template TEXT")
        fault_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(memory_faults)")}
        if "available_tags" not in fault_cols:
            self.conn.execute(
                "ALTER TABLE memory_faults ADD COLUMN available_tags TEXT NOT NULL DEFAULT '[]'")
        with self.conn:
            for fid, name, kind in _DEFAULT_FOLDERS:
                self.conn.execute(
                    "INSERT OR IGNORE INTO folders(id,name,kind) VALUES(?,?,?)",
                    (fid, name, kind),
                )

    def _folder_for(self, typ: str) -> str:
        if typ in ("commitment", "plan"):
            return "commitments"
        if typ in ("fact", "preference", "relationship"):
            return "facts"
        if typ == "observation":
            return "observations"
        return "events"

    # ---- index ----
    def list_folders(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id,name,kind FROM folders ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def list_folder(self, folder_id: str, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """SELECT e.id,e.kind,e.title,e.created_at,ev.text
               FROM entries e
               JOIN entry_folders ef ON ef.entry_id=e.id
               LEFT JOIN events ev ON ev.id=e.id
               WHERE ef.folder_id=? AND e.owner_id=? AND e.scope=?
                 AND (ev.status IS NULL OR ev.status='active')
               ORDER BY e.created_at DESC, e.rowid DESC LIMIT ?""",
            (folder_id, self.owner_id, self.scope, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def read_entry(self, entry_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM episodes WHERE id=? AND owner_id=? AND scope=?",
            (entry_id, self.owner_id, self.scope),
        ).fetchone()
        if row is not None:
            out = dict(row)
            out["_type"] = "episode"
            return out
        row = self.conn.execute(
            "SELECT * FROM events WHERE id=? AND owner_id=? AND scope=?",
            (entry_id, self.owner_id, self.scope),
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["_type"] = "event"
        return out

    def search(self, query: str, limit: int = 20) -> list[dict]:
        like = f"%{query.strip()}%"
        out: list[dict] = []
        for row in self.conn.execute(
                "SELECT id,summary,diary FROM episodes WHERE owner_id=? AND scope=? "
                "AND status='active' AND (summary LIKE ? OR diary LIKE ? OR anchors LIKE ?) "
                "ORDER BY created_at DESC LIMIT ?",
                (self.owner_id, self.scope, like, like, like, limit)):
            out.append({"type": "episode", "id": row["id"],
                        "title": row["summary"], "text": row["diary"]})
        for row in self.conn.execute(
                "SELECT id,title,text FROM events WHERE owner_id=? AND scope=? "
                "AND status='active' AND (title LIKE ? OR text LIKE ?) "
                "ORDER BY created_at DESC LIMIT ?",
                (self.owner_id, self.scope, like, like, limit)):
            out.append({"type": "event", "id": row["id"],
                        "title": row["title"], "text": row["text"]})
        return out[:limit]

    def all_episodes(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM episodes WHERE owner_id=? AND scope=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (self.owner_id, self.scope, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_events(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE owner_id=? AND scope=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (self.owner_id, self.scope, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_memory(self, entry_id: str, mode: str = "soft") -> dict | None:
        mode = mode.lower()
        if mode not in ("soft", "hard", "quarantine"):
            raise ValueError("mode must be soft / hard / quarantine")
        ep = self.conn.execute("SELECT id FROM episodes WHERE id=?", (entry_id,)).fetchone()
        if ep is not None:
            with self.conn:
                if mode == "hard":
                    self.conn.execute("DELETE FROM used_log WHERE episode_id=?", (entry_id,))
                    self.conn.execute("DELETE FROM episodes WHERE id=?", (entry_id,))
                else:
                    self.conn.execute(
                        "UPDATE episodes SET status=? WHERE id=?",
                        ("deleted" if mode == "soft" else "quarantine", entry_id))
            return {"id": entry_id, "type": "episode", "mode": mode}
        ev = self.conn.execute("SELECT id FROM events WHERE id=?", (entry_id,)).fetchone()
        if ev is not None:
            with self.conn:
                if mode == "hard":
                    self.conn.execute("DELETE FROM entry_folders WHERE entry_id=?", (entry_id,))
                    self.conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
                    self.conn.execute("DELETE FROM events WHERE id=?", (entry_id,))
                else:
                    self.conn.execute(
                        "UPDATE events SET status=? WHERE id=?",
                        ("deleted" if mode == "soft" else "quarantine", entry_id))
            return {"id": entry_id, "type": "event", "mode": mode}
        return None

    # ---- write ----
    def add_event(self, data: dict, turn: int | None = None) -> str | None:
        if not isinstance(data, dict):
            return None
        title = _clip(data.get("title") or data.get("text"), 80)
        text = _clip(data.get("text") or title, 400)
        if not title:
            return None
        eid = _id("ev", data)
        typ = _clip(data.get("type") or "event", 24) or "event"
        facets = data.get("facets") or {}
        if not isinstance(facets, dict):
            facets = {}
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO events
                   (id,owner_id,scope,type,title,text,facets,occurred_at,due_at,
                    status,epistemic,source_turn,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (eid, self.owner_id, self.scope, typ, title, text,
                 json.dumps(facets, ensure_ascii=False),
                 data.get("occurred_at"), data.get("due_at"),
                 _clip(data.get("status") or "active", 16) or "active",
                 _clip(data.get("epistemic") or "observed", 16) or "observed",
                 turn, _now()),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO entries(id,owner_id,scope,kind,title,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (eid, self.owner_id, self.scope, typ, title, _now()),
            )
            folder = self._folder_for(typ)
            self.conn.execute(
                "INSERT OR IGNORE INTO entry_folders(entry_id,folder_id,weight) "
                "VALUES(?,?,1.0)",
                (eid, folder),
            )
        return eid

    def add_episode(self, data: dict, turn: int | None = None) -> str | None:
        eid = self._add_episode(data, turn)
        if eid:
            self.last_episode_ids.append(eid)
        return eid

    def _add_episode(self, data: dict, turn: int | None = None) -> str | None:
        if not isinstance(data, dict):
            return None
        summary = _clip(data.get("summary"), 40)
        diary = _clip(data.get("diary"), 120)
        if not summary and not diary:
            return None
        anchors = data.get("anchors") or []
        if not isinstance(anchors, list):
            anchors = [anchors]
        anchors = [str(a) for a in anchors if str(a).strip()]
        eid = _id("ep", data)
        thread_name = (data.get("thread_id") or (anchors[0] if anchors else "")
                       or summary or diary)
        thread_id = _clip(thread_name, 40) or eid
        vals = (_axis(data.get("v")), _axis(data.get("a")),
                _axis(data.get("agency")), _axis(data.get("self_focus")),
                summary, diary, json.dumps(anchors, ensure_ascii=False), turn)

        if self.allow_merge and thread_id != eid:
            row = self.conn.execute(
                "SELECT * FROM episodes WHERE owner_id=? AND scope=? "
                "AND thread_id=? AND status='active' ORDER BY rowid DESC LIMIT 1",
                (self.owner_id, self.scope, thread_id),
            ).fetchone()
            if row is not None:
                revision = int(row["revision"] or 1) + 1
                if revision <= 3:
                    with self.conn:
                        self.conn.execute(
                            "UPDATE episodes SET revision=?,v=?,a=?,agency=?,self_focus=?,"
                            "summary=?,diary=?,anchors=?,source_turn=?,created_at=? "
                            "WHERE id=?",
                            (revision, *vals, _now(), row["id"]),
                        )
                    return row["id"]
                with self.conn:
                    self.conn.execute(
                        "UPDATE episodes SET status='sealed' WHERE id=?", (row["id"],))
                    self.conn.execute(
                        """INSERT INTO episodes
                           (id,owner_id,scope,thread_id,revision,status,parent_id,
                            v,a,agency,self_focus,summary,diary,anchors,source_turn,
                            created_at,last_used_turn,used_count)
                           VALUES(?,?,?,?,1,'active',?,?,?,?,?,?,?,?,?,?,NULL,0)""",
                        (eid, self.owner_id, self.scope, thread_id, row["id"],
                         *vals, _now()),
                    )
                return eid

        with self.conn:
            self.conn.execute(
                """INSERT INTO episodes
                   (id,owner_id,scope,thread_id,revision,status,parent_id,
                    v,a,agency,self_focus,summary,diary,anchors,source_turn,
                    created_at,last_used_turn,used_count)
                   VALUES(?,?,?,?,1,'active',?,?,?,?,?,?,?,?,?,?,NULL,0)""",
                (eid, self.owner_id, self.scope, thread_id,
                 data.get("parent_id"), *vals, _now()),
            )
        return eid

    def add_user_state(self, data: dict | None, turn: int | None = None) -> bool:
        if not isinstance(data, dict):
            return False
        axes = {axis: _axis(data.get(axis)) for axis in AXES if data.get(axis) is not None}
        if not axes:
            return False
        with self.conn:
            self.conn.execute(
                "INSERT INTO user_state(owner_id,scope,v,a,agency,self_focus,"
                "source_turn,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (self.owner_id, self.scope,
                 axes.get("v", 3), axes.get("a", 3),
                 axes.get("agency", 3), axes.get("self_focus", 3),
                 turn, _now()),
            )
        return True

    def ingest(self, events: Iterable[dict] = (), episodes: Iterable[dict] = (),
               used: Iterable[str] = (), user_state: dict | None = None,
               turn: int | None = None) -> dict:
        counts = {"events": 0, "episodes": 0, "used": 0, "user_state": 0}
        self.last_episode_ids = []
        for event in events or ():
            if self.add_event(event, turn):
                counts["events"] += 1
        for episode in episodes or ():
            if self.add_episode(episode, turn):
                counts["episodes"] += 1
        ids = [str(x) for x in (used or ()) if str(x).strip()]
        if ids:
            counts["used"] = self.mark_used(ids, turn)
        if user_state and self.add_user_state(user_state, turn):
            counts["user_state"] = 1
        return counts

    # ---- episode -> objective index links ----
    def bind_anchors(self, objective_triggers: Iterable[str],
                     episode_ids: Iterable[str] | None = None) -> dict[str, int]:
        """Bind episode anchors to the existing MemoryDB trigger vocabulary.

        Anchors are T1..T7 index phrases (<=120 chars). A newly created objective
        in the same turn is allowed because the caller binds after MemoryDB.upsert.
        Missing/invalid anchors are recorded, not raised. A card with no anchors at
        all gets one sentinel row (anchor='') so the next turn can ask for them.
        """
        valid = {str(x).strip() for x in (objective_triggers or ()) if str(x).strip()}
        ids = list(episode_ids if episode_ids is not None else self.last_episode_ids)
        counts = {"bound": 0, "missing": 0, "invalid": 0}
        seen: set[tuple[str, str]] = set()
        bound_ids: list[str] = []
        for eid in ids:
            row = self.conn.execute(
                "SELECT anchors FROM episodes WHERE id=? AND owner_id=? AND scope=?",
                (str(eid), self.owner_id, self.scope),
            ).fetchone()
            if row is None:
                continue
            try:
                anchors = json.loads(row["anchors"] or "[]")
            except (json.JSONDecodeError, TypeError):
                anchors = []
            if not isinstance(anchors, list):
                anchors = [anchors]
            anchors = [str(a).strip() for a in anchors if str(a).strip()]
            now = _now()
            if not anchors:
                counts["missing"] += 1
                self.conn.execute(
                    "INSERT INTO episode_anchors"
                    "(episode_id,anchor,objective_trigger,status,template,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(episode_id,anchor) DO UPDATE SET "
                    "objective_trigger=NULL,status=excluded.status,template=NULL,"
                    "updated_at=excluded.updated_at",
                    (str(eid), "", None, "missing", "", now, now),
                )
                continue
            all_bound = True
            for anchor in anchors:
                seen.add((str(eid), anchor))
                if not anchor_is_valid(anchor):
                    status, target = "invalid", None
                elif anchor in valid:
                    status, target = "bound", anchor
                else:
                    status, target = "missing", None
                if status != "bound":
                    all_bound = False
                template = anchor.split(":", 1)[0] if anchor.startswith("T") else ""
                counts[status] += 1
                self.conn.execute(
                    "INSERT INTO episode_anchors"
                    "(episode_id,anchor,objective_trigger,status,template,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(episode_id,anchor) DO UPDATE SET "
                    "objective_trigger=excluded.objective_trigger,status=excluded.status,"
                    "template=excluded.template,updated_at=excluded.updated_at",
                    (str(eid), anchor, target, status, template, now, now),
                )
            if all_bound:
                bound_ids.append(str(eid))
        if bound_ids:
            marks = ",".join("?" * len(bound_ids))
            self.conn.execute(
                f"UPDATE memory_faults SET state='resolved', updated_at=? "
                f"WHERE episode_id IN ({marks}) AND state!='resolved'",
                [_now(), *bound_ids],
            )
        self.conn.commit()
        return counts

    # ---- index table: clarity ----
    def index_upsert(self, target: str, kind: str, turn: int | None = None,
                     clarity: str = "clear") -> None:
        target = str(target or "").strip()
        if not target:
            return
        now = _now()
        with self.conn:
            self.conn.execute(
                """INSERT INTO memory_index
                   (target,kind,source_turn,clarity,updated_turn,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(target) DO UPDATE SET
                     kind=excluded.kind, source_turn=excluded.source_turn,
                     clarity=excluded.clarity, updated_turn=excluded.updated_turn,
                     updated_at=excluded.updated_at""",
                (target, kind, turn, clarity, turn, now),
            )

    def index_mark(self, targets: Iterable[str], clarity: str,
                   turn: int | None = None) -> int:
        ids = [str(t).strip() for t in (targets or ()) if str(t).strip()]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        with self.conn:
            cur = self.conn.execute(
                f"UPDATE memory_index SET clarity=?, updated_turn=?, updated_at=? "
                f"WHERE target IN ({marks})",
                [clarity, turn, _now(), *ids],
            )
        return cur.rowcount or 0

    def index_clear(self, targets: Iterable[str], turn: int | None = None) -> int:
        """折入当前轮的条目：写回 clear，缺失行补建。"""
        n = 0
        for target in targets or ():
            target = str(target).strip()
            if not target:
                continue
            kind = "episode" if target.startswith("ep_") else "objective"
            self.index_upsert(target, kind, turn, "clear")
            n += 1
        return n

    def index_clarity(self) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT target,clarity FROM memory_index").fetchall()
        return {r["target"]: r["clarity"] for r in rows}

    def index_apply_compression(self, taken_turns: Iterable[int],
                                recent_turns: Iterable[int]) -> dict[str, int]:
        taken = sorted({int(t) for t in (taken_turns or ())})
        recent = sorted({int(t) for t in (recent_turns or ())})
        now = _now()
        counts = {"fuzzy": 0, "clear": 0}
        with self.conn:
            if taken:
                marks = ",".join("?" * len(taken))
                cur = self.conn.execute(
                    f"UPDATE memory_index SET clarity='fuzzy', updated_turn=?, updated_at=? "
                    f"WHERE source_turn IN ({marks})",
                    [max(taken), now, *taken])
                counts["fuzzy"] = cur.rowcount or 0
            if recent:
                marks = ",".join("?" * len(recent))
                cur = self.conn.execute(
                    f"UPDATE memory_index SET clarity='clear', updated_turn=?, updated_at=? "
                    f"WHERE source_turn IN ({marks})",
                    [max(recent), now, *recent])
                counts["clear"] = cur.rowcount or 0
        return counts

    def index_all(self, limit: int = 1000) -> list[dict]:
        rows = self.conn.execute(
            "SELECT target,kind,source_turn,clarity,updated_turn,updated_at "
            "FROM memory_index ORDER BY source_turn, target LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]

    def index_reconcile(self, existing_turns: Iterable[int]) -> int:
        """删除轮次后，把对应记忆位改成 forgotten。"""
        turns = {int(t) for t in (existing_turns or ())}
        rows = self.conn.execute(
            "SELECT target,source_turn FROM memory_index "
            "WHERE source_turn IS NOT NULL AND clarity!='forgotten'").fetchall()
        gone = [r["target"] for r in rows if int(r["source_turn"]) not in turns]
        if gone:
            self.index_mark(gone, "forgotten", None)
        return len(gone)

    def pending_links(self) -> list[dict]:
        """All unbound anchors on active cards, across turns, with fault attempts."""
        rows = self.conn.execute(
            """SELECT pa.episode_id, pa.anchor, pa.status,
                      COALESCE((SELECT MAX(f.attempts) FROM memory_faults f
                                WHERE f.episode_id=pa.episode_id
                                  AND f.state!='resolved'), 0) AS attempts,
                      e.summary, e.source_turn
               FROM episode_anchors pa
               JOIN episodes e ON e.id=pa.episode_id
               WHERE e.owner_id=? AND e.scope=? AND e.status='active'
                 AND pa.status IN ('missing','invalid')
               ORDER BY e.source_turn, pa.episode_id, pa.anchor""",
            (self.owner_id, self.scope),
        ).fetchall()
        return [dict(r) for r in rows]

    def anchor_actions(self) -> dict[str, list[dict]]:
        """Split pending anchors into the three fallback stages.

        notice: first failure, ask the model on the next turn (no extra request)
        repair: already asked once, allow one repair request
        failed: repair also failed, hand over to the fault log
        """
        pending = self.pending_links()
        return {
            "notice": [x for x in pending if int(x["attempts"]) < 2],
            "fresh": [x for x in pending if int(x["attempts"]) == 0],
            "repair": [x for x in pending if int(x["attempts"]) == 1],
            "failed": [x for x in pending if int(x["attempts"]) >= 2],
        }

    def record_fault(self, link: dict, *, turn: int | None = None,
                     state: str = "pending", payload: str = "",
                     detail: str = "", available_tags: Iterable[str] = ()) -> bool:
        """Persist the first on-site payload and clear-tag snapshot."""
        eid = str(link.get("episode_id") or "")
        if not eid:
            return False
        anchor = str(link.get("anchor") or "")
        status = str(link.get("status") or "missing")
        tags = json.dumps(
            sorted({str(x).strip() for x in (available_tags or ()) if str(x).strip()}),
            ensure_ascii=False)
        now = _now()
        with self.conn:
            self.conn.execute(
                """INSERT INTO memory_faults
                   (owner_id,scope,episode_id,anchor,status,state,attempts,turn,
                    detail,payload,available_tags,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,1,?,?,?,?,?,?)
                   ON CONFLICT(episode_id,anchor,status) DO UPDATE SET
                     state=CASE WHEN memory_faults.state='failed' THEN 'failed'
                                ELSE excluded.state END,
                     turn=COALESCE(excluded.turn,memory_faults.turn),
                     detail=CASE WHEN excluded.detail!='' THEN excluded.detail
                                 ELSE memory_faults.detail END,
                     payload=CASE WHEN memory_faults.payload='' THEN excluded.payload
                                  ELSE memory_faults.payload END,
                     available_tags=CASE WHEN excluded.available_tags!='[]'
                                         THEN excluded.available_tags
                                         ELSE memory_faults.available_tags END,
                     updated_at=excluded.updated_at""",
                (self.owner_id, self.scope, eid, anchor, status, state, turn,
                 _clip(detail, 500), _clip(payload, 2000), tags, now, now),
            )
        return True

    def bump_fault(self, episode_id: str, *, turn: int | None = None,
                   state: str = "failed", payload: str = "",
                   detail: str = "") -> int:
        """Increment attempts for one card's unresolved faults."""
        rows = self.conn.execute(
            "SELECT id,payload,detail FROM memory_faults "
            "WHERE episode_id=? AND state!='resolved'",
            (str(episode_id),),
        ).fetchall()
        now = _now()
        with self.conn:
            for row in rows:
                self.conn.execute(
                    "UPDATE memory_faults SET attempts=attempts+1, state=?, "
                    "turn=COALESCE(?,turn), detail=?, payload=?, updated_at=? WHERE id=?",
                    (state, turn, _clip(detail, 500) or row["detail"] or "",
                     row["payload"] or _clip(payload, 2000), now, row["id"]),
                )
        return len(rows)

    def faults(self, state: str | None = None, limit: int = 100) -> list[dict]:
        sql = ("SELECT * FROM memory_faults WHERE owner_id=? AND scope=?")
        args: list[Any] = [self.owner_id, self.scope]
        if state:
            sql += " AND state=?"
            args.append(state)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def resolve_faults(self, episode_ids: Iterable[str]) -> int:
        ids = [str(x) for x in (episode_ids or ()) if str(x).strip()]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        with self.conn:
            cur = self.conn.execute(
                f"UPDATE memory_faults SET state='resolved', updated_at=? "
                f"WHERE episode_id IN ({marks}) AND state!='resolved'",
                [_now(), *ids],
            )
        return cur.rowcount or 0

    def links_for(self, episode_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT episode_id,anchor,objective_trigger,status,template,updated_at "
            "FROM episode_anchors WHERE episode_id=? ORDER BY anchor",
            (str(episode_id),),
        ).fetchall()
        return [dict(r) for r in rows]

    def missing_links(self, episode_ids: Iterable[str] | None = None) -> list[dict]:
        ids = list(episode_ids if episode_ids is not None else self.last_episode_ids)
        missing = []
        for eid in ids:
            missing.extend(x for x in self.links_for(eid) if x["status"] == "missing")
        return missing

    def bad_links(self, episode_ids: Iterable[str] | None = None) -> list[dict]:
        ids = list(episode_ids if episode_ids is not None else self.last_episode_ids)
        bad = []
        for eid in ids:
            bad.extend(x for x in self.links_for(eid) if x["status"] != "bound")
        return bad

    def set_anchors(self, episode_id: str, anchors: Iterable[str]) -> bool:
        cleaned = [str(a).strip() for a in (anchors or ()) if str(a).strip()]
        with self.conn:
            cur = self.conn.execute(
                "UPDATE episodes SET anchors=? WHERE id=? AND owner_id=? AND scope=?",
                (json.dumps(cleaned, ensure_ascii=False), str(episode_id),
                 self.owner_id, self.scope),
            )
            if cur.rowcount:
                self.conn.execute(
                    "DELETE FROM episode_anchors WHERE episode_id=?",
                    (str(episode_id),),
                )
        return bool(cur.rowcount)

    def add_anchors(self, episode_ids: Iterable[str], anchors: Iterable[str]) -> int:
        """Union current-turn MEM triggers into episode anchors."""
        new = [str(a).strip() for a in (anchors or ()) if str(a).strip()]
        if not new:
            return 0
        n = 0
        for eid in episode_ids:
            row = self.conn.execute(
                "SELECT anchors FROM episodes WHERE id=? AND owner_id=? AND scope=?",
                (str(eid), self.owner_id, self.scope),
            ).fetchone()
            if row is None:
                continue
            try:
                old = json.loads(row["anchors"] or "[]")
            except (json.JSONDecodeError, TypeError):
                old = []
            merged = list(dict.fromkeys([str(x) for x in old if str(x).strip()] + new))
            self.conn.execute(
                "UPDATE episodes SET anchors=? WHERE id=?",
                (json.dumps(merged, ensure_ascii=False), str(eid)),
            )
            self.conn.execute(
                "DELETE FROM episode_anchors WHERE episode_id=? AND anchor=''",
                (str(eid),),
            )
            n += 1
        self.conn.commit()
        return n

    def episodes_for(self, anchor: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT episode_id FROM episode_anchors "
            "WHERE anchor=? AND status='bound' ORDER BY episode_id",
            (str(anchor).strip(),),
        ).fetchall()
        return [r["episode_id"] for r in rows]

    def unlink_anchor(self, anchor: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM episode_anchors WHERE anchor=?", (str(anchor).strip(),))
        self.conn.commit()
        return cur.rowcount or 0

    def prune_links(self, objective_triggers: Iterable[str]) -> int:
        """Backend deletion/rename cleanup; never called by the model."""
        valid = {str(x).strip() for x in (objective_triggers or ()) if str(x).strip()}
        rows = self.conn.execute(
            "SELECT DISTINCT objective_trigger FROM episode_anchors "
            "WHERE objective_trigger IS NOT NULL").fetchall()
        removed = 0
        with self.conn:
            for row in rows:
                if row["objective_trigger"] not in valid:
                    cur = self.conn.execute(
                        "DELETE FROM episode_anchors WHERE objective_trigger=?",
                        (row["objective_trigger"],),
                    )
                    removed += cur.rowcount or 0
        return removed

    def mark_used(self, ids: Iterable[str], turn: int | None = None,
                  quote: str = "") -> int:
        seen: set[str] = set()
        n = 0
        with self.conn:
            for eid in ids:
                eid = str(eid).strip()
                if not eid or eid in seen:
                    continue
                seen.add(eid)
                self.conn.execute(
                    "INSERT INTO used_log(episode_id,turn,quote,created_at) VALUES(?,?,?,?)",
                    (eid, turn, _clip(quote, 120), _now()),
                )
                cur = self.conn.execute(
                    "UPDATE episodes SET used_count=used_count+1,last_used_turn=? WHERE id=?",
                    (turn, eid),
                )
                n += 1 if cur.rowcount else 0
        return n

    # ---- read / render ----
    def recent_episodes(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM episodes WHERE owner_id=? AND scope=? AND status='active' "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (self.owner_id, self.scope, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def current_state(self) -> dict[str, int]:
        rows = self.recent_episodes(1)
        if not rows:
            return {axis: 3 for axis in AXES}
        row = rows[0]
        return {axis: _axis(row.get(axis)) for axis in AXES}

    def current_user_state(self) -> dict[str, int]:
        row = self.conn.execute(
            "SELECT v,a,agency,self_focus FROM user_state "
            "WHERE owner_id=? AND scope=? ORDER BY id DESC LIMIT 1",
            (self.owner_id, self.scope),
        ).fetchone()
        if row is None:
            return {}
        return {axis: _axis(row[axis]) for axis in AXES}

    def _candidates(self, limit: int = CANDIDATE_POOL) -> list[tuple[Candidate, dict]]:
        out = []
        for row in self.recent_episodes(limit):
            text = (row.get("summary") or "") + " " + (row.get("diary") or "")
            out.append((
                Candidate(
                    id=row["id"], text=text.strip(),
                    v=row["v"], a=row["a"],
                    agency=row["agency"], self_focus=row["self_focus"],
                    scene="episode",
                    tags=(row.get("status") or "",),
                ),
                row,
            ))
        return out

    def render_block(self, limit: int = 10,
                     exclude_ids: Iterable[str] = (),
                     full_count: int = 0,
                     dedup: bool = True,
                     user_msg: str = "",
                     rng: random.Random | None = None) -> str:
        full_count = max(0, min(limit, full_count))
        blocked = {str(x) for x in exclude_ids}
        clarity = self.index_clarity()
        pairs = [(c, row) for c, row in self._candidates(CANDIDATE_POOL)
                 if c.id not in blocked
                 and (not dedup or clarity.get(c.id, "forgotten") != "clear")]
        by_id = {c.id: row for c, row in pairs}
        cands = [c for c, _ in pairs]

        # 3 条 Δ 小 + 最多 5 条语义命中（不足不补）+ 2 条随机
        delta_sel = select(self.current_state(), cands, limit=3,
                           scene="episode", recent_ids=blocked)
        delta_ids = [x.candidate.id for x in delta_sel][:limit]
        used_ids = set(delta_ids)
        rest = [c for c in cands if c.id not in used_ids]
        scored_semantic = [
            (c, _semantic_score(by_id.get(c.id, {}), user_msg, ROLE_ALIASES))
            for c in rest
        ]
        hits = [item for item in scored_semantic if item[1] > 0]
        hits.sort(key=lambda item: (-item[1], item[0].id))
        semantic_ids = [c.id for c, _ in hits[:5]]
        used_ids.update(semantic_ids)
        rest2 = [c for c in rest if c.id not in used_ids]
        picker = rng or random.Random()
        random_ids = ([c.id for c in picker.sample(rest2, min(2, len(rest2)))]
                      if rest2 else [])
        selected_ids = (delta_ids + semantic_ids + random_ids)[:limit]
        self.last_ids = selected_ids
        self.last_groups = {"delta": delta_ids, "semantic": semantic_ids,
                            "random": random_ids}
        self.last_scores = {}
        for item in delta_sel:
            eid = item.candidate.id
            self.last_scores[eid] = {
                "delta": round(item.score, 4),
                "semantic": _semantic_score(by_id.get(eid, {}), user_msg,
                                            ROLE_ALIASES),
            }
        for eid in semantic_ids:
            self.last_scores[eid] = {
                "delta": 0.0,
                "semantic": _semantic_score(by_id.get(eid, {}), user_msg,
                                            ROLE_ALIASES),
            }
        for eid in random_ids:
            self.last_scores[eid] = {"delta": 0.0, "semantic": 0, "random": True}
        full = delta_sel[:min(full_count, len(delta_ids))]
        full_ids = {x.candidate.id for x in full}
        index = [eid for eid in selected_ids if eid not in full_ids]
        lines = [PROTOCOL, "【记忆候选】"]
        # USER_STATE 只写入 user_state 表作日志，不进入上下文、不参与检索/权重/USED。
        if not selected_ids:
            lines.append("（空）")
        for i, item in enumerate(full, 1):
            row = by_id.get(item.candidate.id, {})
            summary = row.get("summary") or item.candidate.text[:40]
            diary = row.get("diary") or ""
            lines.append(
                f"{i}. {item.candidate.id} | v={item.candidate.v} a={item.candidate.a} "
                f"agency={item.candidate.agency} self_focus={item.candidate.self_focus} | {summary}"
            )
            if diary:
                lines.append(f"   日记：{diary}")
            anchors = row.get("anchors") or "[]"
            try:
                anchor_list = json.loads(anchors) if isinstance(anchors, str) else anchors
            except (json.JSONDecodeError, TypeError):
                anchor_list = []
            if anchor_list:
                lines.append("   锚点：" + "、".join(str(a) for a in anchor_list))
        if index:
            lines.append("【记忆索引】" + "；".join(
                f"{eid}:{(by_id.get(eid) or {}).get('summary', '')}"
                for eid in index
            ))
        return "\n".join(lines)

    def debug_line(self) -> str:
        ev = self.conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
        ep = self.conn.execute("SELECT COUNT(*) AS n FROM episodes").fetchone()["n"]
        used = self.conn.execute("SELECT COUNT(*) AS n FROM used_log").fetchone()["n"]
        return f"【长期记忆】事件 {ev} | 情感卡 {ep} | USED {used}"

    def close(self) -> None:
        self.conn.close()
