"""记忆索引：触发情景 / 摘要，不是关键词。

一条记忆 = 一个触发点 + 若干条目。
索引行只给“触发点:摘要”，正文走 search()。
"""
from .models import MemoryEntry


class MemoryStore:
    def __init__(self, summary_len: int = 20):
        self._items: dict[str, MemoryEntry] = {}
        self.summary_len = summary_len

    def upsert(self, trigger: str, op: str, body: str) -> MemoryEntry:
        e = self._items.setdefault(trigger, MemoryEntry(trigger=trigger))
        if op == "replace":
            e.entries = [body]
        else:
            if body not in e.entries:
                e.entries.append(body)
        e.summary = e.entries[0][: self.summary_len]
        return e

    def get(self, trigger: str) -> MemoryEntry | None:
        return self._items.get(trigger)

    def all(self) -> list[MemoryEntry]:
        return list(self._items.values())

    def index_line(self) -> str:
        if not self._items:
            return ""
        parts = [e.render() for e in self._items.values()]
        return "【记忆】" + "；".join(parts)

    def match(self, text: str) -> list[str]:
        """用户输入命中触发点 → 才允许/要求检索记忆。"""
        return [t for t in self._items if t in text]

    def search(self, trigger: str) -> str:
        e = self._items.get(trigger)
        if not e:
            return "未找到相关记忆。"
        return f"{e.trigger}：" + "；".join(e.entries)
