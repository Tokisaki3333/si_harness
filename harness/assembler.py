"""消息组装：顺序固定，固定前缀永不参与压缩。

[0] system        L1 + 框架 + 可查条目（固定）
[1] user          完整人格卡（固定）
[2] user          【前情摘要】（压缩轮，压缩时变一次）
[3..]            窗口轮：user / 工具结果 / assistant（只追加尾部）
[..] user         【场景】/【状态机】/【记忆·新增】/ 灵活索引（每轮变）
[..] user         本轮输入
"""
from .models import State, Turn


class Assembler:
    def __init__(self, system_parts: list[str], card: str):
        self.system_parts = system_parts
        self.card = card

    def system_text(self) -> str:
        return "\n\n".join(p for p in self.system_parts if p)

    def prefix(self) -> list[dict]:
        return [
            {"role": "system", "content": self.system_text()},
            {"role": "user", "content": self.card},
        ]

    def build(self, user_msg: str, window: list[Turn], *,
              compressed: list[str] | tuple[str, ...] = (),
              scene: str = "",
              state_line: str = "", memory_line: str = "",
              index_line: str = "", memory_prefix: str = "",
              long_term_block: str = "", notice: str = "") -> list[dict]:
        msgs = self.prefix()
        for line in compressed or ():
            msgs.append({"role": "user", "content": line})
        for t in window:
            label = t.display or t.index
            msgs.append({"role": "user", "content": f"[轮{label}] {t.user}"})
            for block in getattr(t, "tools", ()) or ():
                msgs.append({"role": "user", "content": block})
            if t.assistant:
                msgs.append({"role": "assistant", "content": t.assistant})
        # 本轮输入紧接窗口：带轮号，下一轮进入窗口时逐字节一致
        msgs.append({"role": "user",
                     "content": f"[轮{len(window) + 1}] {user_msg}"})
        for block in (scene, state_line, memory_line, index_line, long_term_block):
            if block:
                msgs.append({"role": "user", "content": block})
        if notice:
            msgs.append({"role": "user", "content": notice})
        return msgs
