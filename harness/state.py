"""状态机：只存值、只做 clamp，不做自动转移。

外部（调试入口或上层逻辑）决定何时切 mode；这里提供 set_mode/debug 直接改。
"""
from .models import State

MODES = ("DAILY", "CLOSE", "INTENSE", "EXITING")


class StateMachine:
    def __init__(self, initial: State | None = None,
                 per_turn_limit: int = 15, floor: int = 0, ceiling: int = 100):
        self.state = initial.copy() if initial else State()
        self.per_turn_limit = per_turn_limit
        self.floor, self.ceiling = floor, ceiling
        self.unlock_token = "0"

    def _clamp(self, value: int) -> int:
        return max(self.floor, min(self.ceiling, value))

    def apply(self, delta: dict | None) -> State:
        if not delta:
            return self.state.copy()
        for key in ("affection", "trust", "intensity"):
            if key in delta:
                step = max(-self.per_turn_limit, min(self.per_turn_limit, int(delta[key])))
                setattr(self.state, key, self._clamp(getattr(self.state, key) + step))
        if "mood" in delta and delta["mood"]:
            self.state.mood = str(delta["mood"])
        return self.state.copy()

    def set_mode(self, mode: str) -> State:
        mode = mode.upper()
        if mode not in MODES:
            raise ValueError(f"unknown mode: {mode}")
        self.state.mode = mode
        return self.state.copy()

    def debug(self, **kwargs) -> State:
        """调试入口：直接覆盖任意字段，不做增量。"""
        if "mode" in kwargs:
            self.set_mode(kwargs.pop("mode"))
        for key in ("affection", "trust", "intensity"):
            if key in kwargs:
                setattr(self.state, key, self._clamp(int(kwargs.pop(key))))
        if "mood" in kwargs:
            self.state.mood = str(kwargs.pop("mood"))
        return self.state.copy()

    def snapshot(self) -> State:
        return self.state.copy()

    def to_line(self) -> str:
        s = self.state
        parts = [f"state={s.mode}", f"affection={s.affection}",
                 f"trust={s.trust}", f"mood={s.mood}"]
        if s.mode in ("CLOSE", "INTENSE"):
            parts.append(f"intensity={s.intensity}")
        parts.append(f"unlock={self.unlock_token}")
        return "【状态机】" + "；".join(parts)
