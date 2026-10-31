"""DeepSeek API 客户端：非流式 / 流式、重试、usage 统计。"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class ChatResult:
    message: dict
    usage: dict = field(default_factory=dict)
    finish_reason: str | None = None
    latency: float = 0.0
    attempts: int = 1
    reasoning: str = ""


def _sanitize(value):
    """去掉 JSON 无法编码的孤立代理字符（常见于截断的 emoji）。"""
    if isinstance(value, str):
        return value.encode("utf-8", "ignore").decode("utf-8")
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    return value


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


class DeepSeekClient:
    def __init__(self, api_key: str, model: str = "deepseek-flash",
                 base_url: str = "https://api.deepseek.com/v1/chat/completions",
                 timeout: int = 300, retries: int = 3):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.retries = retries

    def _request(self, body: dict) -> urllib.request.Request:
        return urllib.request.Request(
            self.base_url, data=json.dumps(_sanitize(body)).encode(),
            headers={"Authorization": "Bearer " + self.api_key,
                     "Content-Type": "application/json"})

    def _retry_delay(self, attempt: int) -> float:
        return min(8.0, 0.8 * (2 ** (attempt - 1)))

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             max_tokens: int = 900, temperature: float | None = None,
             tool_choice: str | dict | None = None,
             thinking: dict | None = None) -> ChatResult:
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
        if tools:
            body["tools"] = tools
            body["parallel_tool_calls"] = True
        if temperature is not None:
            body["temperature"] = temperature
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if thinking is not None:
            body["thinking"] = thinking

        last_error = None
        started = time.time()
        for attempt in range(1, self.retries + 1):
            try:
                with urllib.request.urlopen(self._request(body), timeout=self.timeout) as f:
                    data = json.loads(f.read().decode())
                choice = data["choices"][0]
                msg = _sanitize(choice["message"])
                return ChatResult(
                    message=msg, usage=data.get("usage", {}),
                    finish_reason=choice.get("finish_reason"),
                    latency=round(time.time() - started, 2), attempts=attempt,
                    reasoning=msg.get("reasoning_content") or "")
            except urllib.error.HTTPError as e:
                raw = e.read().decode(errors="replace")
                last_error = ApiError(e.code, raw)
                if e.code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    raise last_error
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = ApiError(0, str(e))
                if attempt == self.retries:
                    raise last_error
            time.sleep(self._retry_delay(attempt))
        raise last_error  # pragma: no cover

    def stream(self, messages: list[dict], tools: list[dict] | None = None,
               max_tokens: int = 900):
        """逐段产出文本增量；tool_calls 分片由调用方按 index 拼接。"""
        body = {"model": self.model, "messages": messages,
                "max_tokens": max_tokens, "stream": True}
        if tools:
            body["tools"] = tools
        req = self._request(body)
        with urllib.request.urlopen(req, timeout=self.timeout) as f:
            for raw in f:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                if delta.get("content"):
                    yield delta["content"]

    def stream_chat(self, messages: list[dict], tools: list[dict] | None = None,
                    max_tokens: int = 900, on_delta=None) -> ChatResult:
        """流式请求，逐段回调正文，返回完整 message（含 tool_calls / reasoning）。"""
        body = {"model": self.model, "messages": messages,
                "max_tokens": max_tokens, "stream": True}
        if tools:
            body["tools"] = tools
            body["parallel_tool_calls"] = True
        content, reasoning = [], []
        calls: dict[int, dict] = {}
        usage: dict = {}
        started = time.time()
        with urllib.request.urlopen(self._request(body), timeout=self.timeout) as f:
            for raw in f:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    if delta.get("content"):
                        content.append(delta["content"])
                        if on_delta:
                            on_delta("content", delta["content"])
                    if delta.get("reasoning_content"):
                        reasoning.append(delta["reasoning_content"])
                        if on_delta:
                            on_delta("reasoning", delta["reasoning_content"])
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = calls.setdefault(idx, {
                            "id": "", "type": "function",
                            "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
        msg = {"role": "assistant", "content": "".join(content)}
        if calls:
            msg["tool_calls"] = [calls[i] for i in sorted(calls)]
        if reasoning:
            msg["reasoning_content"] = "".join(reasoning)
        msg = _sanitize(msg)
        return ChatResult(message=msg, usage=usage, finish_reason="stop",
                          latency=round(time.time() - started, 2), attempts=1,
                          reasoning=msg.get("reasoning_content") or "")

    def balance(self) -> dict:
        """查询账户余额；失败返回空字典。"""
        url = "https://api.deepseek.com/user/balance"
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=15) as f:
                return json.loads(f.read().decode())
        except Exception:
            return {}
