"""资料索引：关键词 → 条目，不是摘要。

每条资料 = 条目名 + 关键词集合；新鲜度由外部维护。
索引行只列 lost/stale，fresh 不占位。
"""


class DocIndex:
    def __init__(self, entries: dict[str, list[str]] | None = None,
                 texts: dict[str, str] | None = None):
        self.entries = entries or {}
        self.texts = texts or {}
        self.freshness: dict[str, str] = {name: "fresh" for name in self.entries}

    def lookup(self, query: str) -> str:
        for name in self.entries:
            if name in query:
                return self.texts.get(name, "")
        for name, kws in self.entries.items():
            if any(k in query for k in kws):
                return self.texts.get(name, "")
        return ""

    def mark(self, name: str, level: str) -> None:
        if level not in ("fresh", "stale", "lost"):
            raise ValueError(level)
        if name in self.entries:
            self.freshness[name] = level

    def touch(self, name: str) -> None:
        if name in self.freshness:
            self.freshness[name] = "fresh"

    def match(self, text: str) -> list[str]:
        hits = [name for name, kws in self.entries.items() if any(k in text for k in kws)]
        return hits

    def should_lookup(self, text: str) -> list[str]:
        """命中条目且 freshness 为 lost/stale → 必须查阅。"""
        return [n for n in self.match(text) if self.freshness.get(n) in ("lost", "stale")]

    def index_all(self) -> str:
        """全量索引：条目很短，直接常驻，不做关键词筛选。"""
        if not self.entries:
            return ""
        parts = []
        for name in self.entries:
            lv = self.freshness.get(name, "fresh")
            parts.append(f"L:{name}" if lv == "lost" else f"S:{name}" if lv == "stale" else name)
        return "【索引】" + "；".join(parts)

    def line(self, text: str) -> str:
        hits = self.match(text)
        parts = []
        for name in hits:
            lv = self.freshness.get(name, "fresh")
            if lv == "lost":
                parts.append(f"L:{name}")
            elif lv == "stale":
                parts.append(f"S:{name}")
        return ("【索引】" + ";".join(parts)) if parts else ""

    def catalog(self) -> str:
        return "【可查条目】" + " / ".join(self.entries)
