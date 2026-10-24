"""The agent loop: model call -> tool calls -> results -> model call, until done or step limit."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .memory import Memory, load_project_notes, make_model_summarizer
from .tools import TOOL_SCHEMAS, Toolbox

SYSTEM_PROMPT = """You are a careful coding agent working inside one project folder.
Tools: list_files, read_file, edit_file, run_command. All paths are relative to the project root.

How to work:
1. Explore first: list files, read the relevant code and tests before editing.
2. Make the smallest correct change. Never edit tests to make them pass unless asked.
3. Verify: run the tests or the program after editing.
4. If a tool returns an error, read it and adapt — don't repeat the same call.
5. When finished, reply with a short summary of what you changed and the test result, with no tool calls."""

# ANSI colours for the log
C = {"tool": "\033[36m", "ok": "\033[32m", "err": "\033[31m", "dim": "\033[2m", "info": "\033[35m", "end": "\033[0m"}


@dataclass
class RunStats:
    steps: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    finished: bool = False  # True if the model stopped on its own (not the step limit)
    tools_used: dict = field(default_factory=dict)


class Agent:
    def __init__(self, client, root: Path, approver, max_steps: int = 25, verbose: bool = True,
                 log_dir: Path | None = None, budget_tokens: int = 60_000):
        self.client = client
        self.root = Path(root).resolve()
        self.tools = Toolbox(self.root, approver)
        self.max_steps = max_steps
        self.verbose = verbose
        self.system = SYSTEM_PROMPT + load_project_notes(self.root)
        self.memory = Memory(budget_tokens=budget_tokens, summarizer=make_model_summarizer(client))
        log_dir = log_dir or self.root / ".agent_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / f"run_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"

    # ---- logging ----
    def _log(self, event: str, **data) -> None:
        with self.log_path.open("a") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "event": event, **data}) + "\n")

    def _print(self, colour: str, text: str) -> None:
        if self.verbose:
            print(f"{C[colour]}{text}{C['end']}")

    # ---- loop ----
    def run(self, task: str) -> tuple[str, RunStats]:
        stats = RunStats()
        t0 = time.time()
        self.memory.add({"role": "user", "content": task})
        self._log("user", content=task)
        final_text = ""

        for step in range(1, self.max_steps + 1):
            stats.steps = step
            note = self.memory.compact_if_needed()
            if note:
                self._print("info", f"[memory] {note}")
                self._log("compact", note=note)

            resp = self.client.complete(self.system, self.memory.messages, TOOL_SCHEMAS)
            stats.input_tokens += resp.input_tokens
            stats.output_tokens += resp.output_tokens
            self._log("model", step=step, text=resp.text,
                      tool_calls=[tc.__dict__ for tc in resp.tool_calls],
                      usage=[resp.input_tokens, resp.output_tokens])
            msg = {"role": "assistant", "content": resp.text, "tool_calls": [tc.__dict__ for tc in resp.tool_calls]}
            if resp.raw:
                msg["raw"] = resp.raw
            self.memory.add(msg)
            if resp.text:
                self._print("dim", f"\n[step {step}] {resp.text.strip()}")

            if not resp.tool_calls:  # model is done
                final_text = resp.text
                stats.finished = True
                break

            results = []
            for tc in resp.tool_calls:
                stats.tool_calls += 1
                stats.tools_used[tc.name] = stats.tools_used.get(tc.name, 0) + 1
                self._print("tool", f"→ {tc.name}({_short(tc.args)})")
                out, is_err = self.tools.run(tc.name, tc.args)
                stats.tool_errors += is_err
                self._print("err" if is_err else "ok", "  " + _short(out, 400).replace("\n", "\n  "))
                self._log("tool", step=step, name=tc.name, args=tc.args, output=out, is_error=is_err)
                results.append({"id": tc.id, "name": tc.name, "output": out, "is_error": is_err})
            self.memory.add({"role": "tool", "results": results})
        else:
            final_text = f"Stopped: hit the {self.max_steps}-step limit."
            self._print("err", final_text)

        stats.seconds = round(time.time() - t0, 1)
        self._log("done", stats=stats.__dict__)
        return final_text, stats


def _short(x, n: int = 160) -> str:
    s = x if isinstance(x, str) else json.dumps(x)
    return s if len(s) <= n else s[:n] + "…"
