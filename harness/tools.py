"""工具循环：执行 tool_calls、回传结果、限次、记录违规与漏调。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable

from .client import ChatResult, DeepSeekClient


@dataclass
class ToolRun:
    final: dict
    messages: list[dict]
    calls: list[dict] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    rounds: int = 0
    usage_totals: dict = field(default_factory=dict)
    usage_calls: list[dict] = field(default_factory=list)
    call_hashes: list[list[str]] = field(default_factory=list)
    call_chars: list[list[int]] = field(default_factory=list)
    call_common_chars: list[int] = field(default_factory=list)
    finish_reason: str = ""


def _merge_usage(total: dict, usage: dict) -> None:
    for key in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens"):
        if key in usage:
            total[key] = total.get(key, 0) + int(usage.get(key) or 0)


def run_tools(client: DeepSeekClient, messages: list[dict], tools: list[dict],
              executor: Callable[[str, dict], str], *,
              allowed: set[str] | None = None, max_rounds: int = 4,
              max_tokens: int = 900, stream: bool = False,
              on_delta=None, on_tool=None) -> ToolRun:
    """allowed=None 表示不限制；否则记录越权调用与应调未调。"""
    msgs = list(messages)
    calls, violations, called = [], [], set()
    usage_totals: dict = {}
    usage_calls: list[dict] = []
    call_hashes: list[list[str]] = []
    call_chars: list[list[int]] = []
    call_common_chars: list[int] = []
    result: ChatResult | None = None

    for round_no in range(1, max_rounds + 1):
        hashes = [
            hashlib.sha256(
                (m["role"] + "\x00" + str(m.get("content") or "")).encode("utf-8")
            ).hexdigest()[:12]
            for m in msgs
        ]
        chars = [len(str(m.get("content") or "")) for m in msgs]
        common = 0
        if call_hashes:
            prev = call_hashes[-1]
            while (common < min(len(prev), len(hashes))
                   and prev[common] == hashes[common]):
                common += 1
        call_hashes.append(hashes)
        call_chars.append(chars)
        call_common_chars.append(sum(chars[:common]))
        if stream:
            result = client.stream_chat(msgs, tools=tools, max_tokens=max_tokens,
                                        on_delta=on_delta)
        else:
            result = client.chat(msgs, tools=tools, max_tokens=max_tokens)
        _merge_usage(usage_totals, result.usage)
        usage_calls.append(dict(result.usage))
        tcs = result.message.get("tool_calls") or []
        if not tcs:
            break
        clean_calls = [
            {"id": c.get("id", ""), "type": c.get("type", "function"),
             "function": {"name": (c.get("function") or {}).get("name", ""),
                          "arguments": (c.get("function") or {}).get("arguments", "")}}
            for c in tcs
        ]
        assistant_msg = {"role": "assistant",
                         "content": result.message.get("content") or "",
                         "tool_calls": clean_calls}
        if result.reasoning:
            assistant_msg["reasoning_content"] = result.reasoning
        msgs.append(assistant_msg)
        for call in tcs:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"name": name, "args": args})
            if on_tool:
                on_tool(name, args)
            if allowed is not None and name not in allowed:
                violations.append(name)
                output = "本轮工具策略不允许调用该工具。"
            else:
                called.add(name)
                try:
                    output = executor(name, args)
                except Exception as e:  # 工具失败回传给模型，不中断
                    output = f"工具执行失败：{e}"
            calls[-1]["output"] = str(output)
            msgs.append({"role": "tool", "tool_call_id": call["id"], "content": str(output)})
    else:
        # 达到上限仍想调工具：用最后一次响应
        pass

    missing = sorted(set(allowed or set()) - called)
    final = result.message if result else {}
    return ToolRun(final=final, messages=msgs, calls=calls, violations=violations,
                   missing=missing, rounds=min(round_no, max_rounds),
                   usage_totals=usage_totals, usage_calls=usage_calls,
                   call_hashes=call_hashes, call_chars=call_chars,
                   call_common_chars=call_common_chars,
                   finish_reason=(result.finish_reason if result else ""))
