"""Bug-fix eval: give the agent 5 bug reports on eval/sample_repo and score how many it fixes.

Each bug runs in a fresh temp copy of the repo, so bugs are independent.
A bug counts as FIXED only if:
  1. its test module passes afterwards, and
  2. the test files are byte-for-byte unchanged (no cheating by editing tests), and
  3. no previously-passing test elsewhere started failing (no regressions).

    python eval/run_eval.py                         # anthropic, default model
    python eval/run_eval.py --provider openai --model llama-3.3-70b-versatile
    python eval/run_eval.py --trials 3              # repeat to measure variance
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from harness.agent import Agent  # noqa: E402
from harness.guardrails import Approver  # noqa: E402
from harness.llm import make_client  # noqa: E402

SAMPLE = ROOT / "sample_repo"

# Written like real bug reports: symptom only, no file names or fixes.
BUGS = [
    {"id": "stats-average", "module": "tests.test_stats",
     "report": "Bug: average([2, 4, 6]) returns 6.0 but should return 4."},
    {"id": "text-palindrome", "module": "tests.test_text",
     "report": "Bug: is_palindrome('Never odd or even') returns False. It should ignore case, spaces and punctuation."},
    {"id": "cart-discount", "module": "tests.test_cart",
     "report": "Bug: Cart.total(10) is supposed to apply a 10% discount but returns a negative number."},
    {"id": "dates-leap-year", "module": "tests.test_dates",
     "report": "Bug: is_leap_year(1900) returns True, and February 2100 is reported as having 29 days."},
    {"id": "inventory-shared-state", "module": "tests.test_inventory",
     "report": "Bug: after adding an item to one Inventory(), a brand-new Inventory() already contains that item."},
]
TASK_TEMPLATE = "{report}\n\nFind the cause, fix it in the source code, and run the tests to confirm."


def tests_hash(repo: Path) -> str:
    h = hashlib.sha256()
    for p in sorted((repo / "tests").rglob("*.py")):
        h.update(p.read_bytes())
    return h.hexdigest()


def run_unittest(repo: Path, module: str | None = None) -> tuple[bool, set[str]]:
    """Returns (all_passed, set_of_failing_test_ids)."""
    args = ["-m", "unittest"] + (["-v", module] if module else ["discover", "-v", "-s", "tests", "-t", "."])
    r = subprocess.run([sys.executable, *args], cwd=repo, capture_output=True, text=True, timeout=120)
    failing = {line.split(" ... ")[0] for line in r.stderr.splitlines()
               if line.endswith(("FAIL", "ERROR"))}
    return r.returncode == 0, failing


def eval_bug(bug: dict, client, max_steps: int, verbose: bool, log_root: Path = ROOT / "logs") -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(SAMPLE, repo, ignore=shutil.ignore_patterns("__pycache__", ".agent_logs"))
        before_hash = tests_hash(repo)
        _, failing_before = run_unittest(repo)

        agent = Agent(client, repo, Approver(auto_approve=True), max_steps=max_steps, verbose=verbose,
                      log_dir=log_root / bug["id"])
        t0 = time.time()
        try:
            summary, stats = agent.run(TASK_TEMPLATE.format(report=bug["report"]))
            crash = None
        except Exception as e:  # noqa: BLE001
            summary, stats, crash = "", None, f"{type(e).__name__}: {e}"

        target_ok, _ = run_unittest(repo, bug["module"])
        _, failing_after = run_unittest(repo)
        tests_untouched = tests_hash(repo) == before_hash
        regressions = sorted(failing_after - failing_before)
        return {
            "bug": bug["id"],
            "fixed": target_ok and tests_untouched and not regressions,
            "target_tests_pass": target_ok,
            "tests_untouched": tests_untouched,
            "regressions": regressions,
            "steps": stats.steps if stats else None,
            "tool_calls": stats.tool_calls if stats else None,
            "tool_errors": stats.tool_errors if stats else None,
            "tokens": (stats.input_tokens + stats.output_tokens) if stats else None,
            "seconds": round(time.time() - t0, 1),
            "agent_finished": stats.finished if stats else False,
            "crash": crash,
            "summary": summary[:500],
            "log": str(agent.log_path),
        }


def write_report(results: list[dict], meta: dict, out: Path) -> str:
    fixed = sum(r["fixed"] for r in results)
    n = len(results)
    lines = [f"# Eval results — {meta['model']}", "",
             f"**Fixed {fixed}/{n} bugs ({100 * fixed / n:.0f}%)** · trials={meta['trials']} · "
             f"max_steps={meta['max_steps']} · {meta['date']}", "",
             "| Bug | Trial | Fixed | Tests untouched | Regressions | Steps | Tool calls | Tokens | Time (s) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['bug']} | {r['trial']} | {'✅' if r['fixed'] else '❌'} | "
                     f"{'yes' if r['tests_untouched'] else 'NO'} | {len(r['regressions'])} | {r['steps']} | "
                     f"{r['tool_calls']} | {r['tokens']} | {r['seconds']} |")
    steps = [r["steps"] for r in results if r["steps"]]
    tokens = [r["tokens"] for r in results if r["tokens"]]
    if steps:
        lines += ["", f"Mean steps: {sum(steps) / len(steps):.1f} · Mean tokens: {sum(tokens) / len(tokens):,.0f}"]
    text = "\n".join(lines) + "\n"
    out.write_text(text)
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"])
    ap.add_argument("--model")
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--max-steps", type=int, default=25)
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--only", help="Run one bug id")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    client = make_client(a.provider, a.model, a.effort)
    bugs = [b for b in BUGS if not a.only or b["id"] == a.only]
    results = []
    for trial in range(1, a.trials + 1):
        for bug in bugs:
            print(f"[trial {trial}] {bug['id']} …", end=" ", flush=True)
            r = eval_bug(bug, client, a.max_steps, a.verbose)
            r["trial"] = trial
            results.append(r)
            print("FIXED" if r["fixed"] else "not fixed", f"({r['steps']} steps, {r['seconds']}s)")

    meta = {"model": f"{a.provider}/{client.model}", "trials": a.trials, "max_steps": a.max_steps,
            "date": time.strftime("%Y-%m-%d %H:%M")}
    (ROOT / "results.json").write_text(json.dumps({"meta": meta, "results": results}, indent=2))
    print("\n" + write_report(results, meta, ROOT / "results.md"))


if __name__ == "__main__":
    main()
