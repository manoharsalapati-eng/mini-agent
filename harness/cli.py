"""Command-line entry point.

    python -m harness ./my_project                 # interactive chat
    python -m harness ./my_project -p "fix the failing test"   # one-shot
    python -m harness --demo                       # offline scripted demo, no API key
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .agent import Agent
from .guardrails import Approver
from .llm import make_client


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="harness", description="A small command-line coding agent.")
    ap.add_argument("folder", nargs="?", default=".", help="Project folder the agent may touch")
    ap.add_argument("-p", "--prompt", help="Run one task and exit")
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"])
    ap.add_argument("--model", help="Model name (or set AGENT_MODEL)")
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                    help="Anthropic effort level (or set AGENT_EFFORT); default is the model's own")
    ap.add_argument("--max-steps", type=int, default=25)
    ap.add_argument("--yes", action="store_true", help="Auto-approve writes/commands (blocklist still applies)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--demo", action="store_true", help="Offline scripted demo on a copy of eval/sample_repo")
    a = ap.parse_args(argv)

    if a.demo:
        from .demo import run_demo
        raise SystemExit(run_demo(Approver(auto_approve=a.yes), verbose=not a.quiet))

    root = Path(a.folder).resolve()
    if not root.is_dir():
        ap.error(f"{root} is not a directory")

    try:
        client = make_client(a.provider, a.model, a.effort)
    except RuntimeError as e:
        ap.error(str(e))
    agent = Agent(client, root, Approver(auto_approve=a.yes),
                  max_steps=a.max_steps, verbose=not a.quiet)
    print(f"Agent ready in {root}  (log: {agent.log_path.relative_to(root)})")

    if a.prompt:
        text, stats = agent.run(a.prompt)
        print(f"\n{text}\n\n[{stats.steps} steps, {stats.tool_calls} tool calls, "
              f"{stats.input_tokens + stats.output_tokens} tokens, {stats.seconds}s]")
        return

    print("Type a task. Commands: /exit, /reset, /tokens")
    while True:
        try:
            task = input("\n\033[1myou>\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not task:
            continue
        if task == "/exit":
            break
        if task == "/reset":
            agent.memory.messages.clear()
            print("History cleared.")
            continue
        if task == "/tokens":
            print(f"~{agent.memory.tokens()} tokens in history, {agent.memory.compactions} compactions")
            continue
        try:
            text, stats = agent.run(task)
        except RuntimeError as e:
            print(f"\033[31m{e}\033[0m")
            continue
        print(f"\n\033[1magent>\033[0m {text}\n\033[2m[{stats.steps} steps, {stats.tool_calls} tool calls]\033[0m")


if __name__ == "__main__":
    main()
