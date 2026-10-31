"""启动包：读配置，把存储接到拼接器上，返回 Runtime。"""
from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .assembler import Assembler
from .client import DeepSeekClient
from .config import AppConfig, load_config
from .docindex import DocIndex
from .models import State
from .scene import SceneState
from .state import StateMachine
from .store import MemoryDB, Session
from .long_term import LongTermMemory


@dataclass
class Runtime:
    config: AppConfig
    client: DeepSeekClient
    session: Session
    memory: MemoryDB
    long_term: LongTermMemory
    state: StateMachine
    assembler: Assembler
    index_line: str
    docs: DocIndex = field(default_factory=DocIndex)
    memory_delta: list[str] = field(default_factory=list)
    known_triggers: set = field(default_factory=set)
    known_affect_ids: set = field(default_factory=set)
    expanded_objectives: set = field(default_factory=set)
    expanded_episodes: set = field(default_factory=set)
    affect_full_count: int = 0
    affect_candidate_limit: int = 10
    dedup_enabled: bool = True
    turns_since_merge: int = 0
    scene: SceneState = field(default_factory=SceneState)
    l1_base: str = ""
    l1_key: str = ""
    unlock_key: str = ""
    unlocked: bool = False

    def set_unlocked(self, on: bool) -> None:
        """向状态机盖戳：unlock=<token> 或 unlock=0，system 不动。"""
        self.unlocked = on
        self.state.unlock_token = self.unlock_key if on else "0"

    def build_messages(self, user_msg: str, window: list, *, scene: str = "",
                       compressed: list[str] | tuple[str, ...] = (),
                       notice: str = "") -> list[dict]:
        delta = "【记忆·新增】" + "；".join(self.memory_delta) if self.memory_delta else ""
        objective = self.objective_block(user_msg)
        lt_block = self.long_term.render_block(
            limit=self.affect_candidate_limit,
            full_count=self.affect_full_count,
            dedup=self.dedup_enabled,
            user_msg=user_msg)
        if objective:
            lt_block = objective + "\n" + lt_block
        return self.assembler.build(
            user_msg, window, compressed=compressed,
            scene=scene or self.scene.line(),
            state_line=self.state.to_line(),
            memory_line=delta,
            long_term_block=lt_block,
            notice=notice)

    def objective_block(self, user_msg: str) -> str:
        """Strict trigger hits only: up to 10 index lines; no padding, no full text."""
        items = self.memory.all()
        if self.dedup_enabled:
            clarity = self.long_term.index_clarity()
            items = [m for m in items
                     if clarity.get(m["trigger"], "forgotten") != "clear"]
        if not items:
            return ""
        def parts(trigger: str):
            body = re.sub(r"^T[1-7]:", "", str(trigger or "").strip())
            return [p.strip() for p in re.split(r"[+|]", body) if p.strip()]
        def score(item):
            ps = parts(item.get("trigger", ""))
            if not ps:
                return 0.0
            return sum(1 for p in ps if p and re.search(re.escape(p), user_msg)) / len(ps)
        ranked = [m for m in sorted(
            items, key=lambda m: (-score(m), m.get("trigger", "")))
            if score(m) > 0]
        if not ranked:
            return ""
        lines = ["【客观候选·一级】"]
        for m in ranked[:10]:
            mark = "已展开" if m["trigger"] in self.expanded_objectives else "未展开"
            lines.append(
                f"- [{mark}]mem:{m.get('id')} {m['trigger']} | {m.get('summary', '')}")
        return "\n".join(lines)

    def sync_memory(self) -> None:
        for m in self.memory.all():
            t = m["trigger"]
            if t not in self.known_triggers:
                self.memory_delta.append(f"{t}:{m['summary']}")
                self.known_triggers.add(t)

    def merge_memory(self) -> None:
        self.memory_delta = []
        self.turns_since_merge = 0
        self.known_affect_ids.clear()
        self.expanded_objectives.clear()
        self.expanded_episodes.clear()

    def bind_affect_anchors(self) -> dict[str, int]:
        """Bind this turn's episode anchors to the active MemoryDB triggers."""
        triggers = {m["trigger"] for m in self.memory.all()}
        return self.long_term.bind_anchors(triggers)

    def record_turn(self, user: str, assistant: str, turn: int,
                    tools: list[str] | tuple[str, ...] = ()) -> tuple[int, int]:
        seq_u, seq_a = self.session.append_turn(
            user, assistant, turn=turn, tools=tools)
        self.session.snapshot(seq_a, asdict(self.state.state))
        return seq_u, seq_a

    def close(self) -> None:
        self.session.close()
        self.memory.close()
        self.long_term.close()


def bootstrap(config_path: str | Path) -> Runtime:
    cfg = load_config(config_path)
    l1 = ""
    if cfg.l1_path:
        l1 = Path(cfg.l1_path).read_text(encoding="utf-8-sig").strip()
    l1_key = ""
    if cfg.l1_key_path and Path(cfg.l1_key_path).exists():
        l1_key = Path(cfg.l1_key_path).read_text(encoding="utf-8-sig").strip()
    card = Path(cfg.card_path).read_text(encoding="utf-8-sig").strip()
    index = json.loads(Path(cfg.index_path).read_text(encoding="utf-8-sig"))
    docs_raw = {}
    if Path(cfg.docs_path).exists():
        docs_raw = json.loads(Path(cfg.docs_path).read_text(encoding="utf-8-sig"))
    entries = {n: v.get("keywords", [n]) for n, v in docs_raw.items()} or index
    texts = {n: v.get("text", "") for n, v in docs_raw.items()}
    docs = DocIndex(entries, texts)
    index_line = "【可查条目】" + " / ".join(entries) if entries else ""

    assembler = Assembler([l1, cfg.frame, index_line], card)
    session = Session(cfg.data_root, cfg.session_id, cfg.bucket_size)
    memory = MemoryDB(cfg.data_root, cfg.session_id)
    client = DeepSeekClient(cfg.api_key, model=cfg.model, base_url=cfg.base_url,
                            timeout=cfg.timeout, retries=cfg.retries)
    rt = Runtime(config=cfg, client=client, session=session, memory=memory,
                 long_term=LongTermMemory(cfg.data_root, cfg.session_id,
                                          allow_merge=cfg.allow_merge),
                 state=StateMachine(State()), assembler=assembler,
                 index_line=index_line, docs=docs, l1_base=l1,
                 l1_key=l1_key, unlock_key=cfg.unlock_key)
    rt.known_triggers = {m["trigger"] for m in memory.all()}
    return rt
