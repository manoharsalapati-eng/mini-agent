"""Model clients. Zero dependencies — plain urllib so the harness is easy to read.

Internal (provider-neutral) message format used everywhere in the harness:
    {"role": "user", "content": "text"}
    {"role": "assistant", "content": "text", "tool_calls": [{"id", "name", "args"}]}
    {"role": "tool", "results": [{"id", "name", "output", "is_error"}]}
Each client converts this to its own wire format.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


def _post(url: str, headers: dict, body: dict, retries: int = 4) -> dict:
    data = json.dumps(body).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:500]
            if e.code in (429, 500, 502, 503, 529) and attempt < retries:
                time.sleep(2 ** attempt)  # exponential backoff
                continue
            raise RuntimeError(f"API error {e.code}: {msg}") from None
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Network error: {e.reason}") from None
    raise RuntimeError("unreachable")


class AnthropicClient:
    def __init__(self, model: str, api_key: str | None = None, max_tokens: int = 4096):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError("Set ANTHROPIC_API_KEY")
        self.max_tokens = max_tokens

    @staticmethod
    def _convert(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["args"]})
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no text)"}]})
            elif m["role"] == "tool":
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["output"], "is_error": r.get("is_error", False)}
                    for r in m["results"]]})
        return out

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        body = {
            "model": self.model, "max_tokens": self.max_tokens, "system": system,
            "messages": self._convert(messages),
            "tools": [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools],
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        d = _post("https://api.anthropic.com/v1/messages", headers, body)
        resp = LLMResponse(input_tokens=d["usage"]["input_tokens"], output_tokens=d["usage"]["output_tokens"])
        for b in d["content"]:
            if b["type"] == "text":
                resp.text += b["text"]
            elif b["type"] == "tool_use":
                resp.tool_calls.append(ToolCall(b["id"], b["name"], b["input"]))
        return resp


class OpenAICompatClient:
    """Works with OpenAI, Groq, Google Gemini (OpenAI endpoint), OpenRouter, Ollama, LM Studio…"""

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "none")
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")

    @staticmethod
    def _convert(system: str, messages: list[dict]) -> list[dict]:
        out = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"id": tc["id"], "type": "function",
                                          "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
                                         for tc in m["tool_calls"]]
                out.append(msg)
            elif m["role"] == "tool":
                for r in m["results"]:
                    out.append({"role": "tool", "tool_call_id": r["id"], "content": r["output"]})
        return out

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        body = {"model": self.model, "messages": self._convert(system, messages),
                "tools": [{"type": "function", "function": t} for t in tools]}
        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        d = _post(f"{self.base_url}/chat/completions", headers, body)
        msg = d["choices"][0]["message"]
        usage = d.get("usage") or {}
        resp = LLMResponse(text=msg.get("content") or "",
                           input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0))
        for i, tc in enumerate(msg.get("tool_calls") or []):
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc["function"]["arguments"]}  # surfaced to model as an error by the tool layer
            resp.tool_calls.append(ToolCall(tc.get("id") or f"call_{i}", tc["function"]["name"], args))
        return resp


class ScriptedClient:
    """Deterministic fake model for tests: replays a list of LLMResponse objects."""

    def __init__(self, script: list[LLMResponse]):
        self.script = list(script)
        self.calls: list[list[dict]] = []

    def complete(self, system, messages, tools) -> LLMResponse:
        self.calls.append([dict(m) for m in messages])
        return self.script.pop(0) if self.script else LLMResponse(text="Done.")


def make_client(provider: str, model: str | None):
    if provider == "anthropic":
        return AnthropicClient(model or os.environ.get("AGENT_MODEL", "claude-sonnet-4-5"))
    if provider == "openai":
        return OpenAICompatClient(model or os.environ.get("AGENT_MODEL", "gpt-4o-mini"))
    raise ValueError(f"Unknown provider: {provider}")
