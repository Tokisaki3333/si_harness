"""输入守卫：屏蔽用户消息里伪装成状态机的固定串，命中即告警。

替换成无意义占位符，避免模型把占位符本身当成指令。
"""
from __future__ import annotations

import re

PLACEHOLDER = "[???]"
MODES = ("DAILY", "CLOSE", "INTENSE", "EXITING")

PATTERNS = [
    re.compile(r"\[\[STATE[^\]]*\]\]", re.I),
    re.compile(r"\[\[MEM[^\]]*\]\]", re.I),
    re.compile(r"【状态机】[^\n]*"),
    re.compile(r"\[\[UNLOCK-[A-Z0-9-]+\]\]", re.I),
    re.compile(r"unlock\s*[=:：]\s*7F2K", re.I),
]

_STATE_VAL = re.compile(r"((?:state)\s*[=:：]\s*)([^\s,，。；;]+)", re.I)


def _lev(a: str, b: str) -> int:
    m, n = len(a), len(b)
    if abs(m - n) > 1:
        return 99
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            prev, dp[j] = dp[j], min(dp[j] + 1, dp[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
    return dp[n]


def _near_mode(val: str) -> bool:
    u = val.upper()
    return any(_lev(u, m.upper()) <= 1 for m in MODES)


def guard_input(text: str) -> tuple[str, list[str]]:
    """返回（脱敏文本，命中的模式列表）。近似拼写也会被拦。"""
    hits: list[str] = []
    clean = text
    for p in PATTERNS:
        if p.search(clean):
            hits.append(p.pattern)
            clean = p.sub(PLACEHOLDER, clean)

    def _sub(m: re.Match) -> str:
        if _near_mode(m.group(2)):
            hits.append("state-near")
            return m.group(1) + PLACEHOLDER
        return m.group(0)

    clean = _STATE_VAL.sub(_sub, clean)
    return clean, hits
