"""响应解析：正文 + 状态行 + 旧 [[MEM]] + 长期记忆 [[EVENT]]/[[EPISODE]]/[[USED]]。"""
import json
import re

from .models import Parsed

_STATE_RE = re.compile(r"^\[\[STATE\s+(.*?)\]\]$", re.S)
_MEM_RE = re.compile(r"^\[\[MEM\s+(.*?)\]\]$", re.S)
_MEM_OP_RE = re.compile(r"trigger=(.+?)\s+op=(append|replace)\s+body=(.+)$", re.S)
_TAG_RE = re.compile(r"^\[\[(EVENT|EPISODE)\s+(.+?)\]\]\s*$")
_USED_RE = re.compile(r"^\[\[USED\s+(.+?)\]\]\s*$")
_ANCHOR_RE = re.compile(r"^\[\[ANCHOR\s+(.+?)\]\]\s*$")
_ANCHOR_FIX_RE = re.compile(r"^(ep_[0-9A-Za-z]+)\s+(.+)$")
_USER_STATE_RE = re.compile(r"^\[\[USER_STATE\s+(.*?)\]\]\s*$")


def _parse_state(inner: str) -> tuple[dict, bool, str | None]:
    delta, ack, loc = {}, False, None
    for part in re.split(r"[；;|\s]+", inner.strip()):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k == "exit":
            ack = v in ("1", "true", "yes")
            continue
        if k == "loc":
            loc = v
            continue
        if k in ("affection", "trust", "intensity"):
            try:
                delta[k] = int(v.replace("+", ""))
            except ValueError:
                continue
        elif k == "mood":
            delta[k] = v
    return delta, ack, loc


def _nonempty_indices(lines: list[str]) -> list[int]:
    return [i for i, l in enumerate(lines) if l.strip()]


_INNER_RE = re.compile(r"【心念】\s*(.*?)(?:\n【正文】|\n\s*\n|$)", re.S)


def parse(text: str) -> Parsed:
    inner = ""
    m = _INNER_RE.search(text)
    if m:
        inner = m.group(1).strip()
        text = text[:m.start()] + text[m.end():]
    text = text.replace("【正文】", "", 1)
    lines = text.splitlines()

    state_delta, mem_ops, ack, scene_loc = None, [], False, None
    events: list[dict] = []
    episodes: list[dict] = []
    used: list[str] = []
    user_state: dict = {}
    anchor_fixes: dict[str, list[str]] = {}
    drop: set[int] = set()

    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue

        m = _STATE_RE.match(s)
        if m:
            d, a, loc = _parse_state(m.group(1))
            state_delta, ack = d, ack or a
            if loc:
                scene_loc = loc
            drop.add(i)
            continue

        m = _MEM_RE.match(s)
        if m:
            mem_inner = m.group(1).strip()
            if mem_inner.lower() != "none":
                mo = _MEM_OP_RE.match(mem_inner)
                if mo:
                    mem_ops.append({"trigger": mo.group(1).strip(),
                                    "op": mo.group(2),
                                    "body": mo.group(3).strip()})
            drop.add(i)
            continue

        m = _TAG_RE.match(s)
        if m:
            drop.add(i)
            raw = m.group(2).strip()
            if raw.lower() == "none":
                continue
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            if m.group(1) == "EVENT":
                events.append(data)
            else:
                episodes.append(data)
            continue

        m = _USED_RE.match(s)
        if m:
            drop.add(i)
            raw = m.group(1).strip()
            if raw.lower() != "none":
                used.extend(x for x in re.split(r"[,;\s]+", raw) if x)
            continue

        m = _ANCHOR_RE.match(s)
        if m:
            drop.add(i)
            raw = m.group(1).strip()
            if raw.lower() != "none":
                fix = _ANCHOR_FIX_RE.match(raw)
                if fix:
                    eid = fix.group(1)
                    items = [a.strip() for a in re.split(r"[,，]", fix.group(2))
                             if a.strip()]
                    bucket = anchor_fixes.setdefault(eid, [])
                    for anchor in items:
                        if anchor not in bucket:
                            bucket.append(anchor)
            continue

        m = _USER_STATE_RE.match(s)
        if m:
            drop.add(i)
            raw = m.group(1).strip()
            if raw.lower() != "none":
                for part in re.split(r"[；;|\s]+", raw):
                    if "=" not in part:
                        continue
                    k, v = part.split("=", 1)
                    if k.strip() in ("v", "a", "agency", "self_focus"):
                        try:
                            user_state[k.strip()] = max(1, min(5, int(v.strip())))
                        except ValueError:
                            continue
            continue

    body = "\n".join(l for i, l in enumerate(lines) if i not in drop).strip()
    return Parsed(body=body, state_delta=state_delta, mem_ops=mem_ops, ack=ack,
                  scene_loc=scene_loc, inner=inner, events=events,
                  episodes=episodes, used=used, user_state=user_state,
                  anchor_fixes=anchor_fixes)
