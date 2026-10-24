"""Conversation memory: history, trimming/summarising, and project notes (AGENTS.md)."""
from __future__ import annotations

import json
from pathlib import Path

NOTES_FILES = ("AGENTS.md", "CLAUDE.md", ".agent/notes.md")


def load_project_notes(root: Path, max_chars: int = 8000) -> str:
    for name in NOTES_FILES:
        p = root / name
        if p.is_file():
            text = p.read_text(errors="replace")[:max_chars]
            return f"\n\n<project_notes file=\"{name}\">\n{text}\n</project_notes>"
    return ""


def estimate_tokens(messages: list[dict]) -> int:
    return len(json.dumps(messages)) // 4  # ~4 chars per token; good enough for budgeting


class Memory:
    """Holds the message list and keeps it under a token budget in two stages:

    1. Shrink old tool outputs (cheap, keeps structure; most of the bulk is file dumps/test logs).
    2. If still too big, summarise everything before the last `keep_recent` messages with the model,
       and replace it with one summary message.
    """

    def __init__(self, budget_tokens: int = 60_000, keep_recent: int = 8, summarizer=None):
        self.messages: list[dict] = []
        self.budget = budget_tokens
        self.keep_recent = keep_recent
        self.summarizer = summarizer  # callable(list[dict]) -> str
        self.compactions = 0

    def add(self, msg: dict) -> None:
        self.messages.append(msg)

    def tokens(self) -> int:
        return estimate_tokens(self.messages)

    def compact_if_needed(self) -> str | None:
        if self.tokens() <= self.budget:
            return None
        # Compacting edits history, and provider thinking blocks are bound to the exact history that
        # produced them. Drop the raw blocks so each client rebuilds plain text + tool calls instead.
        for m in self.messages:
            m.pop("raw", None)
        # Stage 1: clip tool results outside the recent window
        cutoff = max(0, len(self.messages) - self.keep_recent)
        for m in self.messages[:cutoff]:
            if m["role"] == "tool":
                for r in m["results"]:
                    if len(r["output"]) > 300:
                        r["output"] = r["output"][:300] + "\n…[old output trimmed]"
        if self.tokens() <= self.budget:
            self.compactions += 1
            return "trimmed old tool outputs"
        # Stage 2: summarise. Cut at an assistant message so tool_call/result pairs stay together
        # and the new list starts user(summary) -> assistant, which every API accepts.
        cut = next((i for i in range(cutoff, 0, -1) if self.messages[i]["role"] == "assistant"), None)
        if cut is None:
            return None
        old, recent = self.messages[:cut], self.messages[cut:]
        summary = self.summarizer(old) if self.summarizer else _fallback_summary(old)
        self.messages = [{"role": "user", "content": f"[Summary of earlier conversation]\n{summary}"}] + recent
        self.compactions += 1
        return f"summarised {len(old)} old messages"


def _fallback_summary(msgs: list[dict]) -> str:
    """No-model summary: the user's requests and which tools were used on what."""
    lines = []
    for m in msgs:
        if m["role"] == "user":
            lines.append(f"- User: {m['content'][:300]}")
        elif m["role"] == "assistant":
            for tc in m.get("tool_calls", []):
                lines.append(f"- Called {tc['name']}({json.dumps(tc['args'])[:120]})")
    return "\n".join(lines[-40:])


SUMMARY_PROMPT = ("Summarise this coding-agent conversation so work can continue. Keep: the user's goal, "
                  "files read/changed and what changed, test results, decisions, and what is left to do. "
                  "Be concise (under 300 words).")


def make_model_summarizer(client, max_chars: int = 60_000):
    def summarize(msgs: list[dict]) -> str:
        transcript = json.dumps(msgs)[:max_chars]
        r = client.complete(SUMMARY_PROMPT, [{"role": "user", "content": transcript}], [])
        return r.text or _fallback_summary(msgs)
    return summarize
