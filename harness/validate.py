"""告警与校验：末尾协议缺失时补一次短请求。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .client import DeepSeekClient
from .models import Parsed
from .parser import parse
from .long_term import anchor_is_valid

REPAIR_PROMPT = (
    "你的上一条回复缺少末尾协议行。不要重写正文；根据本轮互动给出增量，只输出两行：\n"
    "[[STATE affection=+N trust=+N mood=… loc=当前地点 exit=0]]\n"
    "[[MEM none]]\n"
    "增量范围 -15..+15，没有变化写 +0。"
)

MEMORY_REPAIR_PROMPT = (
    "你的上一条回复里 EVENT / EPISODE 协议行 JSON 不完整。不要重写正文；"
    "只输出修正后的协议行，每行必须闭合：\n"
    '[[EVENT {"type":"fact","title":"标题","text":"事实","people":[]}]]\n'
    '[[EPISODE {"thread_id":"可选","anchors":["T4:张三+批评+李四"],"v":3,"a":3,"agency":3,"self_focus":3,'
    '"summary":"摘要","diary":"第一人称日记"}]]\n'
    "没有对应内容就写 [[EVENT none]] / [[EPISODE none]]。"
)

_ANCHOR_FIX_RE = re.compile(r"^\[\[ANCHOR\s+(ep_[0-9a-f]+)\s+(.+?)\]\]\s*$")
_MEM_NONE_RE = re.compile(r"^\s*\[\[MEM\s+none\]\]\s*$", re.I)

USED_REPAIR_PROMPT = (
    "你上一条回复的 [[USED ...]] 里有既不在上下文、也不在本轮 memory_read 里的条目。"
    "不要重写正文；只输出一行修正后的 USED，只能列 clear 或本轮已展开的 id；"
    "没有就写 [[USED none]]。"
)


def repair_used_protocol(client: DeepSeekClient, messages: list[dict], text: str,
                         used: list[str], clarity: dict[str, str],
                         read_targets: set[str] | list[str],
                         max_retries: int = 1) -> tuple[list[str], bool]:
    """USED 只能引用 clear 或本轮已展开的条目；否则补一轮只修 USED。"""
    read = set(read_targets or ())
    bad = [x for x in used if clarity.get(x, "forgotten") != "clear"
           and x not in read]
    if not bad:
        return list(used), False
    prompt = USED_REPAIR_PROMPT + "\n不可用：" + "、".join(bad)
    for _ in range(max_retries):
        repair_msgs = list(messages) + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": prompt},
        ]
        result = client.chat(repair_msgs, max_tokens=100,
                             thinking={"type": "disabled"})
        p2 = parse(result.message.get("content") or "")
        if p2.used:
            kept = [x for x in p2.used
                    if clarity.get(x, "forgotten") == "clear" or x in read]
            return kept, True
    return [], True


def mem_present(parsed: Parsed, text: str) -> bool:
    """A valid MEM op or an explicit [[MEM none]] counts as provided."""
    return bool(parsed.mem_ops) or bool(
        any(_MEM_NONE_RE.match(line.strip()) for line in text.splitlines())
    )


def repair_mem_protocol(client: DeepSeekClient, messages: list[dict], text: str,
                        parsed: Parsed, max_retries: int = 1) -> tuple[Parsed, bool]:
    """Missing MEM after the grace turn: ask only for MEM lines."""
    if mem_present(parsed, text):
        return parsed, False
    prompt = (
        "你上一轮没有输出有效的 [[MEM ...]]。不要重写正文；只输出 MEM 行：\n"
        "先补上一轮缺失的客观记忆，再补本轮；每行一条，索引用 T1..T7，body 与索引匹配。\n"
        "确实没有就写 [[MEM none]]。"
    )
    for _ in range(max_retries):
        repair_msgs = list(messages) + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": prompt},
        ]
        result = client.chat(repair_msgs, max_tokens=300,
                             thinking={"type": "disabled"})
        extra = result.message.get("content") or ""
        p2 = parse(extra)
        if p2.mem_ops or mem_present(p2, extra):
            parsed.mem_ops.extend(p2.mem_ops)
            return parsed, True
    return parsed, False


def repair_anchor_protocol(client: DeepSeekClient, messages: list[dict], text: str,
                           bad: list[dict], triggers: list[str],
                           max_retries: int = 1) -> dict[str, list[str]]:
    """Anchors 超长/整句/未命中 objective 时，补一轮只修 anchors。"""
    if not bad:
        return {}
    bad_text = "\n".join(
        f"- {x.get('episode_id')}: {x.get('anchor')} ({x.get('status')})"
        for x in bad
    )
    trigger_text = "、".join(str(t) for t in triggers) or "（当前没有 objective trigger）"
    prompt = (
        "你上一轮 EPISODE.anchors 格式或索引失败。不要重写正文；只输出修正行：\n"
        "[[ANCHOR ep_xxxxxxxx T1:小明+头发]]\n"
        "索引必须标注 T1..T7 模板，元素用 + 分隔（T6 用 |）：\n"
        "T1 实体 T2 属性 T3 关系 T4 事件 T5 时空 T6 别名 T7 复合；不写整句。\n"
        f"可用 objective trigger：{trigger_text}\n"
        f"失败项：\n{bad_text}\n"
        "如果没有可修正的 anchor，输出 [[ANCHOR none]]。"
    )
    for _ in range(max_retries):
        repair_msgs = list(messages) + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": prompt},
        ]
        result = client.chat(repair_msgs, max_tokens=300,
                             thinking={"type": "disabled"})
        extra = result.message.get("content") or ""
        fixes: dict[str, list[str]] = {}
        for line in extra.splitlines():
            match = _ANCHOR_FIX_RE.match(line.strip())
            if not match:
                continue
            eid = match.group(1)
            anchors = [a.strip() for a in re.split(r"[,，]", match.group(2))]
            anchors = [a for a in anchors if anchor_is_valid(a)]
            if anchors:
                fixes[eid] = anchors[:3]
        if fixes:
            return fixes
    return {}


def _broken_memory_tag(text: str) -> bool:
    for line in text.splitlines():
        s = line.strip()
        if not (s.startswith("[[EVENT") or s.startswith("[[EPISODE")):
            continue
        if s.lower().endswith("none]]"):
            continue
        if not s.endswith("]]"):
            return True
        raw = s[s.index(" "):-2].strip() if " " in s else ""
        try:
            json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return True
    return False


def repair_memory_protocol(client: DeepSeekClient, messages: list[dict],
                           text: str, parsed: Parsed,
                           max_retries: int = 1) -> tuple[Parsed, bool]:
    """EVENT/EPISODE JSON 损坏时补一轮协议，类似工具报错后重试。"""
    if not _broken_memory_tag(text):
        return parsed, False
    for _ in range(max_retries):
        repair_msgs = list(messages) + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": MEMORY_REPAIR_PROMPT},
        ]
        result = client.chat(repair_msgs, max_tokens=600,
                             thinking={"type": "disabled"})
        extra = result.message.get("content") or ""
        p2 = parse(extra)
        if p2.events or p2.episodes:
            parsed.events.extend(p2.events)
            parsed.episodes.extend(p2.episodes)
            parsed.used.extend(x for x in p2.used if x not in parsed.used)
            if p2.user_state:
                parsed.user_state.update(p2.user_state)
            return parsed, True
    return parsed, False


@dataclass
class ValidationResult:
    parsed: Parsed
    repaired: bool = False
    warnings: list[str] = field(default_factory=list)
    repair_usage: dict = field(default_factory=dict)


def ensure_protocol(client: DeepSeekClient, messages: list[dict], text: str,
                    max_retries: int = 1, allow_missing: bool = False) -> ValidationResult:
    p = parse(text)
    if p.state_delta is not None:
        return ValidationResult(p, repaired=False, warnings=[])

    warnings = ["missing_state"]
    if allow_missing:
        return ValidationResult(p, repaired=False, warnings=warnings)
    for _ in range(max_retries):
        repair_msgs = list(messages) + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": REPAIR_PROMPT},
        ]
        result = client.chat(repair_msgs, max_tokens=80,
                             thinking={"type": "disabled"})
        extra = result.message.get("content") or ""
        p2 = parse(extra)
        if p2.state_delta is not None:
            # 补行响应只带 STATE/MEM；正文里的协议行必须保留
            p2.events = p.events + p2.events
            p2.episodes = p.episodes + p2.episodes
            p2.mem_ops = p.mem_ops + p2.mem_ops
            p2.used = p.used + [x for x in p2.used if x not in p.used]
            if p.user_state:
                p2.user_state = {**p.user_state, **p2.user_state}
            if p.anchor_fixes:
                p2.anchor_fixes = {**p.anchor_fixes, **p2.anchor_fixes}
            return ValidationResult(p2, repaired=True, warnings=warnings,
                                    repair_usage=result.usage)
        warnings.append("repair_failed")
    return ValidationResult(p, repaired=False, warnings=warnings)
