"""Model clients. Zero dependencies — plain urllib so the harness is easy to read.

Internal (provider-neutral) message format used everywhere in the harness:
    {"role": "user", "content": "text"}
    {"role": "assistant", "content": "text", "tool_calls": [{"id", "name", "args"}], "raw": [...]}
    {"role": "tool", "results": [{"id", "name", "output", "is_error"}]}
Each client converts this to its own wire format. "raw" is optional: the provider's own content blocks,
replayed unchanged so things like Anthropic thinking blocks survive the round trip.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


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
    raw: list | None = None  # provider content blocks to replay as-is (see module docstring)


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


# Models that accept the server-side `fallbacks` parameter on the Claude API.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class AnthropicClient:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, api_key: str | None = None, max_tokens: int = 16_000,
                 effort: str | None = None):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError("Set ANTHROPIC_API_KEY (or put it in a .env file — see .env.example)")
        self.max_tokens = max_tokens
        self.effort = effort or os.environ.get("AGENT_EFFORT")  # low | medium | high | xhigh | max

    @staticmethod
    def _convert(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                if m.get("raw"):  # replay the model's own blocks unchanged (keeps thinking blocks valid)
                    out.append({"role": "assistant", "content": m["raw"]})
                    continue
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
        if self.model in FALLBACK_MODELS:
            # If a safety classifier declines, let the API retry on a fallback model instead of stopping.
            body["fallbacks"] = "default"
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
        if self.effort:
            body["output_config"] = {"effort": self.effort}
        d = _post(self.URL, headers, body)
        if d.get("stop_reason") == "refusal":
            category = (d.get("stop_details") or {}).get("category")
            raise RuntimeError(f"Model declined the request (refusal, category={category})")
        resp = LLMResponse(input_tokens=d["usage"]["input_tokens"], output_tokens=d["usage"]["output_tokens"],
                           raw=d["content"])
        for b in d["content"]:
            if b["type"] == "text":
                resp.text += b["text"]
            elif b["type"] == "tool_use":
                resp.tool_calls.append(ToolCall(b["id"], b["name"], b["input"]))
        if d.get("stop_reason") == "max_tokens" and not resp.tool_calls:
            resp.text += "\n[response cut off at max_tokens]"
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


def load_dotenv(*paths: Path) -> None:
    """Tiny .env reader (KEY=value lines). Never overrides variables that are already set."""
    paths = paths or (Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env")
    for p in paths:
        if not p.is_file():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.removeprefix("export ").split("=", 1)
            value = value.strip().strip("'\"")
            if value:
                os.environ.setdefault(key.strip(), value)


def make_client(provider: str, model: str | None, effort: str | None = None):
    load_dotenv()
    if provider == "anthropic":
        return AnthropicClient(model or os.environ.get("AGENT_MODEL", "claude-opus-5-5"), effort=effort)
    if provider == "openai":
        return OpenAICompatClient(model or os.environ.get("AGENT_MODEL", "gpt-4o-mini"))
    raise ValueError(f"Unknown provider: {provider}")
