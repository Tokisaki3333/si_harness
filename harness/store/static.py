"""只读区：人格卡 + 关键词索引。"""
from __future__ import annotations

import json
from pathlib import Path


class StaticStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._card: str | None = None
        self._index: dict[str, list[str]] | None = None

    def card(self) -> str:
        if self._card is None:
            path = self.root / "card.md"
            if not path.exists():
                raise FileNotFoundError(f"缺少人格卡：{path}")
            self._card = path.read_text(encoding="utf-8")
        return self._card

    def index_entries(self) -> dict[str, list[str]]:
        if self._index is None:
            path = self.root / "index.json"
            if not path.exists():
                raise FileNotFoundError(f"缺少索引：{path}")
            self._index = json.loads(path.read_text(encoding="utf-8"))
        return self._index

    def index_line(self) -> str:
        names = list(self.index_entries())
        return "【可查条目】" + " / ".join(names) if names else ""
