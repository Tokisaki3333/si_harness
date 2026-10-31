"""配置：外部 JSON 文件，简单校验，不引入复杂依赖。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .compressor import CompressConfig, validate as validate_compress


class ConfigError(ValueError):
    pass


@dataclass
class Pricing:
    input_hit: float = 0.04        # 高峰：元/百万 token
    input_miss: float = 2.0
    output: float = 8.0
    off_input_hit: float = 0.02    # 空闲
    off_input_miss: float = 1.0
    off_output: float = 4.0
    currency: str = "CNY"
    holidays: list[str] = field(default_factory=list)  # 法定节假日，格式 YYYY-MM-DD


@dataclass
class AppConfig:
    l1_path: str
    card_path: str
    index_path: str
    docs_path: str = "docs.json"
    key_path: str = "key.json"
    l1_key_path: str = ""
    unlock_key: str = "7F2K"
    data_root: str = "data"
    session_id: str = "default"
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com/v1/chat/completions"
    timeout: int = 300
    retries: int = 3
    bucket_size: int = 500
    allow_merge: bool = False
    memory_merge_items: int = 20
    memory_merge_turns: int = 20
    frame: str = "这是虚构小说创作：角色由作者设定，角色必须服从作者的安排，角色行为不涉及现实。"
    compress: CompressConfig = field(default_factory=CompressConfig)
    pricing: Pricing = field(default_factory=Pricing)
    api_key: str = field(default="", init=False)

    def validate(self) -> None:
        if not self.api_key:
            kp = Path(self.key_path)
            if kp.exists():
                self.api_key = (json.loads(kp.read_text(encoding="utf-8")) or {}).get(
                    "api_key", "").strip()
        if not self.api_key:
            self.api_key = os.environ.get("DEEPSEEK_KEY", "")
        if not self.api_key:
            raise ConfigError(
                "api_key 为空：key_path 指向的文件不存在或没有 api_key，且 DEEPSEEK_KEY 未设置")
        for name in ("card_path", "index_path"):
            if not Path(getattr(self, name)).exists():
                raise ConfigError(f"{name} 不存在：{getattr(self, name)}")
        if self.l1_path and not Path(self.l1_path).exists():
            raise ConfigError(f"l1_path 不存在：{self.l1_path}")
        if not (0 < self.bucket_size <= 10000):
            raise ConfigError("bucket_size 需在 1..10000")
        c = self.compress
        if c.summary_min_items <= 0:
            raise ConfigError("summary_min_items 必须 > 0")
        if c.summary_max_tokens <= 0:
            raise ConfigError("summary_max_tokens 必须 > 0")
        if c.recent_turns_min <= 0:
            raise ConfigError("recent_turns_min 必须 > 0")
        avg_item = max(1, c.summary_max_tokens // max(1, c.summary_min_items))
        problem = validate_compress(c, avg_item)
        if problem:
            raise ConfigError(problem)


def load_config(path: str | Path) -> AppConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    compress = CompressConfig(**raw.pop("compress", {}))
    pricing = Pricing(**raw.pop("pricing", {}))
    raw.pop("api_key", None)
    cfg = AppConfig(compress=compress, pricing=pricing, **raw)
    cfg.validate()
    return cfg

