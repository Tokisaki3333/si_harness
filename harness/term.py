"""终端配色：低对比度、暗背景，非 TTY 自动关闭。"""
from __future__ import annotations

import os
import sys

_ENABLED = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def enable_ansi() -> None:
    if os.name == "nt":
        os.system("")  # 打开 Windows 控制台的 ANSI 支持


class C:
    RESET = "\033[0m" if _ENABLED else ""
    TEXT = "\033[38;5;252m" if _ENABLED else ""      # 正文：浅灰白
    LABEL = "\033[38;5;109m" if _ENABLED else ""     # 标签/表头：柔和蓝灰
    DEBUG = "\033[38;5;244m" if _ENABLED else ""     # 调试：中灰
    USER = "\033[38;5;180m" if _ENABLED else ""      # 输入提示：米色
    THINK = "\033[38;5;146m" if _ENABLED else ""     # 思考：淡紫灰
    ERROR = "\033[38;5;167m" if _ENABLED else ""     # 错误：柔和红
    BG = "\033[48;5;235m" if _ENABLED else ""        # 背景：深灰，不是纯黑

    @classmethod
    def paint(cls, text: str, color: str) -> str:
        return f"{color}{text}{cls.RESET}" if color else text
