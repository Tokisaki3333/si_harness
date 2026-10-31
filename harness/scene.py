"""场景状态：时间随轮次推进，地点由模型在状态行里用 loc= 修改。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SceneState:
    location: str = "帝江号·舷窗前"
    minutes: int = 120

    def advance(self, user_text: str = "", step_minutes: int = 5) -> None:
        self.minutes += step_minutes

    def line(self) -> str:
        h, m = divmod(self.minutes, 60)
        return f"【场景】{self.location}；已相处约{h}小时{m}分"
