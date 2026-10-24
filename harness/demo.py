"""Offline demo: a scripted "model" fixes the cart-discount bug in a temp copy of eval/sample_repo.

No API key and no network. The model's decisions are pre-written, but every tool call goes through the
real harness — sandbox, blocklist, approval prompt, file edits, test runs and the JSONL log — so you can
see the loop working end to end before plugging in a real model.

    python -m harness --demo          # asks before the edit and each command
    python -m harness --demo --yes    # auto-approve
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .agent import Agent
from .llm import LLMResponse, ScriptedClient, ToolCall

SAMPLE = Path(__file__).resolve().parents[1] / "eval" / "sample_repo"
TASK = "Bug: Cart.total(10) is supposed to apply a 10% discount but returns a negative number. Fix it."


def demo_script(python: str = "python") -> list[LLMResponse]:
    def step(text, name, **args):
        return LLMResponse(text=text, tool_calls=[ToolCall(f"demo_{name}_{len(args)}", name, args)])

    return [
        step("Let me look at the project layout first.", "list_files"),
        step("Run the cart tests to reproduce the bug.", "run_command", command=f"{python} -m unittest tests.test_cart"),
        step("test_ten_percent fails. Reading the cart code.", "read_file", path="shopkit/cart.py"),
        step("discount_percent is a percentage (10 = 10%) but is used as a fraction. Dividing by 100.",
             "edit_file", path="shopkit/cart.py",
             old_str="(1 - discount_percent)", new_str="(1 - discount_percent / 100)"),
        step("Re-running the cart tests to verify.", "run_command", command=f"{python} -m unittest tests.test_cart"),
        LLMResponse(text="Fixed shopkit/cart.py: Cart.total() treated discount_percent as a fraction, so 10 meant "
                         "1000% off. It now divides by 100. tests.test_cart passes (3/3)."),
    ]


def run_demo(approver, verbose: bool = True) -> int:
    if not SAMPLE.is_dir():
        print("The demo needs eval/sample_repo; run it from a git clone of mini-agent.")
        return 1
    python = "python" if shutil.which("python") else "python3"
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "sample_repo"
        shutil.copytree(SAMPLE, repo, ignore=shutil.ignore_patterns("__pycache__", ".agent_logs"))
        print("\033[35m[demo] scripted model, no API key used; every tool call below runs for real\033[0m")
        print(f"\033[35m[demo] working on a temp copy: {repo}\033[0m\n")
        print(f"\033[1myou>\033[0m {TASK}")
        agent = Agent(ScriptedClient(demo_script(python)), repo, approver, verbose=verbose)
        text, stats = agent.run(TASK)
        print(f"\n\033[1magent>\033[0m {text}\n\033[2m[{stats.steps} steps, {stats.tool_calls} tool calls, "
              f"{stats.tool_errors} tool errors, {stats.seconds}s]\033[0m")
        return 0 if stats.finished and stats.tool_errors == 0 else 1
