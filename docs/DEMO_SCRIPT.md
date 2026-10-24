# 60–90 second demo video script

A ready-made silent 60 s video is already in [`docs/demo.mp4`](demo.mp4) (rendered by `scripts/make_demo_video.py`). Use this script if you also want a narrated recording with a real model.

Record with Loom, OBS or your OS screen recorder. Terminal font ≥ 16 pt, dark theme, window ~100 columns.
Put the link on the 🎥 line at the top of the README.

**Before recording:** `cp .env.example .env`, add your key, and do a dry run once so nothing surprises you.
Reset the sample repo afterwards with `git checkout eval/sample_repo`.

| Time | On screen | Say |
|---|---|---|
| 0:00–0:10 | README top | "This is mini-agent, a coding agent harness in about 600 lines of plain Python with no dependencies. It's the loop, tools, memory and guardrails that turn a model into an agent, like a tiny Claude Code." |
| 0:10–0:20 | `python -m unittest tests.test_cart` in `eval/sample_repo` → failure | "Here's a small shop package with a seeded bug: a 10% discount returns a negative total." |
| 0:20–0:50 | `python -m harness ./eval/sample_repo -p "Cart.total(10) should give 10% off but returns a negative number. Fix it."` | "I give it only the symptom. It lists files, runs the tests, reads the code… and before it writes anything it shows me the diff and asks. Commands need approval too, and things like `rm -rf` are blocked even in auto mode." Press `y` on the edit and the test run. |
| 0:50–1:05 | Final answer + `[N steps, … tokens]` line, then `ls .agent_logs` / `head` of the JSONL | "Tests pass. Every model call and tool call is logged as JSON so I can debug its decisions." |
| 1:05–1:25 | `eval/results.md` | "To measure it, I seeded 5 bugs. A fix only counts if the target tests pass, the test files are untouched and nothing else breaks. It fixed X out of 5 with <model>." |
| 1:25–1:30 | Repo URL | "Code, eval and write-up are on GitHub." |

No key yet? Record the same flow with `python -m harness --demo` and say it's a scripted replay through the real harness.
