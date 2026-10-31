"""上下文压缩：窗口 + 单一摘要块。

流程：
1. 上下文超过 context_limit 时触发；
2. keep = 从最新往前，先满足 recent_turns_min，再继续到 recent_tokens_max；
3. taken = 更早的、尚未压缩过的轮次，一次请求上报；
4. 摘要结果按 summary_min_items / summary_max_tokens 裁剪；
5. 最终 = 固定前导 + 摘要 + keep。

摘要块本身不再参与下一次压缩请求的输入轮次，只作为上下文前缀。
API 失败不本地回退：抛 CompressError，由调用方提示手动重试。
"""
from dataclasses import dataclass, field

from .models import Turn

TOKENS_PER_CHAR = 0.75

SUMMARY_PROMPT = (
    "把以上对话逐轮压成摘要。要求：\n"
    "1 每一轮对话输出一条，共 {n} 条，顺序与输入严格一致；\n"
    "2 每条一行，以“- ”开头；\n"
    "3 只压缩以上对话轮次；前面的压缩轮只作背景，不要重写、不要压缩、不要合并进输出；\n"
    "4 保留：地点、事件、承诺、约定、关系变化、重要称呼；\n"
    "5 不写分析、不评价、不复述原文台词；\n"
    "6 只输出摘要行，不要正文，不要状态行。"
)


class CompressError(RuntimeError):
    pass


def tl(s: str) -> int:
    return int(len(s) * TOKENS_PER_CHAR)


@dataclass
class CompressConfig:
    context_limit: int = 400000
    recent_turns_min: int = 8
    recent_tokens_max: int = 8000
    summary_min_items: int = 16
    summary_max_tokens: int = 3000
    fixed_tokens: int = 6000
    min_compress_turns: int = 3
    max_summary_items: int = 12


@dataclass
class CompressResult:
    recent: list[Turn]
    lines: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    taken: list[Turn] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return "\n".join(self.lines)


def keep_count(recent: list[Turn], cfg: CompressConfig) -> int:
    """从最新往前：先满足最少轮数，再继续到 token 上限。"""
    keep, tok = 0, 0
    for t in reversed(recent):
        add = tl(t.user) + tl(t.assistant)
        if keep >= cfg.recent_turns_min and tok + add > cfg.recent_tokens_max:
            break
        keep += 1
        tok += add
    return keep


def trim_summary(lines: list[str], cfg: CompressConfig) -> list[str]:
    """保证至少 summary_min_items 条；超 token 时先合并旧条，最后才截断。"""
    out = [l.strip() for l in lines if l.strip()]
    while tl("\n".join(out)) > cfg.summary_max_tokens and len(out) > cfg.summary_min_items:
        out = out[:-2] + [out[-2] + "；" + out[-1]]
    if tl("\n".join(out)) > cfg.summary_max_tokens:
        keep = out[:cfg.summary_min_items] or out[:1]
        per = max(1, cfg.summary_max_tokens // len(keep))
        out = [k[: int(per / TOKENS_PER_CHAR)] for k in keep]
    return out


def _turns_msgs(turns: list[Turn]) -> list[dict]:
    out = []
    for t in turns:
        label = t.display or t.index
        out.append({"role": "user", "content": f"[轮{label}] {t.user}"})
        for block in getattr(t, "tools", ()) or ():
            out.append({"role": "user", "content": block})
        if t.assistant:
            out.append({"role": "assistant", "content": t.assistant})
    return out


def _compress_api(client, prefix_msgs: list[dict], turns: list[Turn],
                  cfg: CompressConfig, tools=None) -> tuple[list[str], dict]:
    msgs = list(prefix_msgs) + [
        {"role": "user", "content": SUMMARY_PROMPT.format(n=len(turns))}]
    res = client.chat(msgs, tools=tools,
                      max_tokens=max(800, len(turns) * 80,
                                     cfg.summary_max_tokens * 2))
    text = (res.message.get("content") or "").strip()
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise CompressError("压缩返回空摘要")
    return lines[:len(turns)], res.usage


def maybe_compress(recent: list[Turn], lines: list[str], cfg: CompressConfig,
                   client=None, prefix_msgs: list[dict] | None = None,
                   tools=None) -> CompressResult:
    """前 m 条要压，后 n 条保留；压缩请求只带前 m 条，不带后 n 条。"""
    usage: dict = {}
    raw = sum(tl(t.user) + tl(t.assistant) for t in recent)
    total = cfg.fixed_tokens + raw + tl("\n".join(lines))
    keep_n = min(cfg.recent_turns_min, len(recent))
    take_m = len(recent) - keep_n
    over_budget = total > cfg.context_limit
    over_tokens = raw > cfg.recent_tokens_max
    if not ((over_budget or over_tokens) and take_m >= cfg.min_compress_turns):
        return CompressResult(recent=recent, lines=list(lines))

    taken = recent[:take_m]
    keep = recent[take_m:]
    if not taken:
        return CompressResult(recent=recent, lines=list(lines))

    if client is None or prefix_msgs is None:
        raise CompressError("压缩需要 API client 与上下文前缀")

    head = list(prefix_msgs)
    for line in lines:
        head.append({"role": "user", "content": line})
    try:
        new_lines, used = _compress_api(
            client, head + _turns_msgs(taken), taken, cfg, tools=tools)
    except CompressError:
        raise
    except Exception as e:
        raise CompressError(f"压缩请求失败：{e}") from e

    summaries = list(lines) + list(new_lines)
    while len(summaries) > cfg.max_summary_items:
        summaries.pop(0)
    usage.update({k: v for k, v in used.items() if isinstance(v, int)})
    events = [{"before": len(recent), "taken": len(taken), "left": len(keep)}]
    return CompressResult(recent=keep, lines=summaries,
                          events=events, usage=usage, taken=taken,
                          new_lines=new_lines)


def validate(cfg: CompressConfig, avg_item_tokens: int) -> str | None:
    after = cfg.fixed_tokens + cfg.recent_tokens_max + cfg.summary_max_tokens
    if after >= cfg.context_limit:
        return (f"压缩后 {after} tok 不小于触发点 {cfg.context_limit}；"
                f"调大 context_limit 或调小 recent_tokens_max / summary_max_tokens")
    return None
