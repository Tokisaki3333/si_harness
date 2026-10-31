"""共享数据结构。所有模块只依赖这里，彼此不直接 import。"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class State:
    mode: str = "DAILY"          # DAILY / CLOSE / INTENSE / EXITING
    affection: int = 68
    trust: int = 62
    intensity: int = 0
    mood: str = "平静"

    def copy(self) -> "State":
        return State(self.mode, self.affection, self.trust, self.intensity, self.mood)


@dataclass
class MemoryEntry:
    trigger: str
    summary: str = ""
    entries: list[str] = field(default_factory=list)

    def render(self) -> str:
        return f"{self.trigger}:{self.summary or (self.entries[-1] if self.entries else '')}"


@dataclass
class Parsed:
    body: str
    state_delta: Optional[dict] = None
    mem_ops: list[dict] = field(default_factory=list)
    ack: bool = False
    scene_loc: Optional[str] = None
    inner: str = ""
    events: list[dict] = field(default_factory=list)
    episodes: list[dict] = field(default_factory=list)
    used: list[str] = field(default_factory=list)
    user_state: dict = field(default_factory=dict)
    anchor_fixes: dict = field(default_factory=dict)


@dataclass
class Turn:
    user: str
    assistant: str = ""
    index: int = 0
    tools: list[str] = field(default_factory=list)
    display: int = 0
