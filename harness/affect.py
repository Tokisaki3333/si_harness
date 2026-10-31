"""结构候选：只按 v/a/agency/self_focus 的 Δ 给候选，不判定情感路径。

当前阶段不标 path；候选只带 Δ、距离、tag 和 scene 证据。
是否属于“自责/被爱/冷静掌控”等路径，等离线或人工标注后再加，不由本模块猜。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

AXES = ("v", "a", "agency", "self_focus")


@dataclass(frozen=True)
class Candidate:
    id: str
    text: str
    v: int
    a: int
    agency: int
    self_focus: int
    scene: str = "monologue"
    tags: tuple[str, ...] = ()
    anchor: str = ""


@dataclass(frozen=True)
class Selected:
    candidate: Candidate
    delta: dict[str, int]
    score: float
    evidence: tuple[str, ...]


def _raw_delta(candidate: Candidate, state: dict[str, int]) -> dict[str, int]:
    return {axis: getattr(candidate, axis) - int(state[axis]) for axis in AXES}


def transition(candidate: Candidate, state: dict[str, int]) -> dict[str, int]:
    raw = _raw_delta(candidate, state)
    return {axis: max(-1, min(1, raw[axis])) for axis in AXES}


def _score(candidate: Candidate, state: dict[str, int]) -> float:
    distance = sum(abs(getattr(candidate, axis) - int(state[axis])) for axis in AXES)
    change = sum(1 for axis in AXES if getattr(candidate, axis) != int(state[axis]))
    return round(
        0.70 * (1 - min(distance, 12) / 12)
        + 0.30 * (change / 4),
        4,
    )


def select(state: dict[str, int], candidates: Sequence[Candidate],
           limit: int = 5, scene: str = "monologue",
           recent_ids: Sequence[str] = ()) -> list[Selected]:
    state = {axis: int(state[axis]) for axis in AXES}
    blocked = set(recent_ids)
    scored: list[Selected] = []
    for candidate in candidates:
        if candidate.scene != scene or candidate.id in blocked:
            continue
        delta = transition(candidate, state)
        evidence = (
            f"Δ=({delta['v']:+d},{delta['a']:+d},{delta['agency']:+d},{delta['self_focus']:+d})",
            f"|Δ|={sum(abs(delta[axis]) for axis in AXES)}",
            f"tag={','.join(candidate.tags) or '-'}",
            f"scene={candidate.scene}",
        )
        scored.append(Selected(candidate, delta, _score(candidate, state), evidence))

    scored.sort(key=lambda item: (-item.score, item.candidate.id))
    seen: set[str] = set()
    result: list[Selected] = []
    for item in scored:
        key = item.candidate.id or item.candidate.text
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def remember_selected(recent_ids: Sequence[str], selected: Sequence[Selected],
                      window: int = 5) -> list[str]:
    """DEPRECATED: 短时去重未接线，保留供精调召回器参考；运行时不要调用。"""
    ids = [item.candidate.id for item in selected] + list(recent_ids)
    out: list[str] = []
    for cid in ids:
        if cid not in out:
            out.append(cid)
    return out[:window]


def apply_selected(state: dict[str, int], item: Selected) -> dict[str, int]:
    """DEPRECATED: 四维是模型对本轮自身状态的评分，不应由召回结果反向改写。"""
    out = {axis: int(state[axis]) for axis in AXES}
    for axis in AXES:
        step = item.delta[axis]
        if step:
            out[axis] = max(1, min(5, out[axis] + (1 if step > 0 else -1)))
    return out
