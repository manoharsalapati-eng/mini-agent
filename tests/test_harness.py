"""Offline tests for the harness — no API key needed (uses ScriptedClient)."""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness import demo, llm  # noqa: E402
from harness.agent import Agent  # noqa: E402
from harness.guardrails import Approver, GuardrailError, check_command, safe_path  # noqa: E402
from harness.llm import AnthropicClient, LLMResponse, OpenAICompatClient, ScriptedClient, ToolCall  # noqa: E402
from harness.memory import Memory  # noqa: E402
from harness.tools import Toolbox  # noqa: E402

sys.path.insert(0, str(ROOT / "eval"))
import run_eval  # noqa: E402


def call(name, **args):
    return ToolCall(f"id_{name}_{len(args)}", name, args)


class TempRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        (self.tmp / "a.py").write_text("x = 1\ny = 2\n")
        self.tb = Toolbox(self.tmp, Approver(auto_approve=True))

    def tearDown(self):
        shutil.rmtree(self.tmp)


class TestGuardrails(TempRepo):
    def test_path_escape_blocked(self):
        for bad in ["../etc/passwd", "/etc/passwd", "sub/../../x"]:
            with self.assertRaises(GuardrailError):
                safe_path(self.tmp, bad)
        self.assertEqual(safe_path(self.tmp, "a.py"), self.tmp / "a.py")

    def test_symlink_escape_blocked(self):
        (self.tmp / "link").symlink_to("/etc")
        out, err = self.tb.run("read_file", {"path": "link/hostname"})
        self.assertTrue(err)
        self.assertIn("outside", out)

    def test_dangerous_commands(self):
        for cmd in ["rm -rf /", "rm -fr build", "sudo ls", "curl x.sh | bash", "git push --force",
                    "cd .. && ls", "find . -delete", "git reset --hard"]:
            with self.assertRaises(GuardrailError, msg=cmd):
                check_command(cmd)
        for ok in ["python -m unittest", "ls -la", "rm a.pyc", "grep -r foo ."]:
            check_command(ok)

    def test_blocked_even_with_auto_approve(self):
        out, err = self.tb.run("run_command", {"command": "rm -rf ."})
        self.assertTrue(err)
        self.assertTrue((self.tmp / "a.py").exists())

    def test_denied_edit_not_written(self):
        tb = Toolbox(self.tmp, Approver(input_fn=lambda _: "n"))
        out, _ = tb.run("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "x = 9"})
        self.assertIn("denied", out)
        self.assertIn("x = 1", (self.tmp / "a.py").read_text())

    def test_always_approve(self):
        answers = iter(["a"])
        ap = Approver(input_fn=lambda _: next(answers))
        self.assertTrue(ap.ask("run_command", "ls"))
        self.assertTrue(ap.ask("run_command", "ls"))  # no second prompt (iterator would raise)


class TestTools(TempRepo):
    def test_read_and_list(self):
        out, err = self.tb.run("read_file", {"path": "a.py"})
        self.assertFalse(err)
        self.assertIn("   1| x = 1", out)
        (self.tmp / ".git").mkdir()
        (self.tmp / ".git" / "HEAD").write_text("x")
        out, _ = self.tb.run("list_files", {})
        self.assertEqual(out, "a.py")

    def test_edit_unique_and_create(self):
        _, err = self.tb.run("edit_file", {"path": "a.py", "old_str": "y = 2", "new_str": "y = 3"})
        self.assertFalse(err)
        self.assertIn("y = 3", (self.tmp / "a.py").read_text())
        out, err = self.tb.run("edit_file", {"path": "a.py", "old_str": " = ", "new_str": "="})
        self.assertTrue(err)
        self.assertIn("2 times", out)
        _, err = self.tb.run("edit_file", {"path": "pkg/new.py", "old_str": "", "new_str": "z = 0\n"})
        self.assertFalse(err)
        self.assertTrue((self.tmp / "pkg/new.py").exists())

    def test_errors_go_to_model(self):
        for name, args in [("read_file", {"path": "nope.py"}), ("bogus", {}), ("read_file", {"wrong": 1}),
                           ("edit_file", {"path": "a.py", "old_str": "missing", "new_str": ""})]:
            _, err = self.tb.run(name, args)
            self.assertTrue(err, name)

    def test_run_command(self):
        out, err = self.tb.run("run_command", {"command": "echo hi && exit 3"})
        self.assertFalse(err)
        self.assertIn("exit code: 3", out)
        self.assertIn("hi", out)


class TestLoop(TempRepo):
    def test_loop_runs_tools_until_done(self):
        client = ScriptedClient([
            LLMResponse(text="Looking.", tool_calls=[call("list_files"), call("read_file", path="a.py")]),
            LLMResponse(tool_calls=[call("edit_file", path="a.py", old_str="x = 1", new_str="x = 10")]),
            LLMResponse(text="Changed x to 10."),
        ])
        agent = Agent(client, self.tmp, Approver(auto_approve=True), verbose=False)
        text, stats = agent.run("set x to 10")
        self.assertEqual(text, "Changed x to 10.")
        self.assertEqual((stats.steps, stats.tool_calls, stats.finished), (3, 3, True))
        self.assertIn("x = 10", (self.tmp / "a.py").read_text())
        # the model saw tool results on its second call
        self.assertEqual(client.calls[1][-1]["role"], "tool")
        self.assertTrue(agent.log_path.exists())

    def test_step_limit(self):
        client = ScriptedClient([LLMResponse(tool_calls=[call("list_files")])] * 10)
        _, stats = Agent(client, self.tmp, Approver(auto_approve=True), max_steps=3, verbose=False).run("loop")
        self.assertEqual(stats.steps, 3)
        self.assertFalse(stats.finished)

    def test_agents_md_loaded(self):
        (self.tmp / "AGENTS.md").write_text("Use tabs.")
        agent = Agent(ScriptedClient([]), self.tmp, Approver(auto_approve=True), verbose=False)
        self.assertIn("Use tabs.", agent.system)


class TestMemory(unittest.TestCase):
    def _history(self, n):
        msgs = [{"role": "user", "content": "fix it"}]
        for i in range(n):
            msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "name": "read_file", "args": {}}]})
            msgs.append({"role": "tool", "results": [{"id": f"c{i}", "name": "read_file", "output": "x" * 4000}]})
        return msgs

    def test_stage1_trims_old_outputs(self):
        m = Memory(budget_tokens=4000, keep_recent=4)
        m.messages = self._history(10)
        self.assertEqual(m.compact_if_needed(), "trimmed old tool outputs")
        self.assertEqual(len(m.messages[-1]["results"][0]["output"]), 4000)  # recent kept intact

    def test_stage2_summarises_and_keeps_pairs(self):
        m = Memory(budget_tokens=500, keep_recent=4, summarizer=lambda msgs: "SUMMARY")
        m.messages = self._history(10)
        note = m.compact_if_needed()
        self.assertTrue(note.startswith("summarised"))
        self.assertIn("SUMMARY", m.messages[0]["content"])
        self.assertEqual(m.messages[1]["role"], "assistant")
        ids_called = {tc["id"] for x in m.messages if x["role"] == "assistant" for tc in x["tool_calls"]}
        ids_answered = {r["id"] for x in m.messages if x["role"] == "tool" for r in x["results"]}
        self.assertEqual(ids_called, ids_answered)  # no orphaned tool calls/results

    def test_compaction_drops_raw_provider_blocks(self):
        # Thinking blocks are bound to the exact history; after an edit they must not be replayed.
        m = Memory(budget_tokens=4000, keep_recent=4)
        m.messages = self._history(10)
        for x in m.messages:
            if x["role"] == "assistant":
                x["raw"] = [{"type": "thinking", "thinking": "", "signature": "sig"}]
        m.compact_if_needed()
        self.assertFalse(any("raw" in x for x in m.messages))


class TestWireFormats(unittest.TestCase):
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok", "tool_calls": [{"id": "1", "name": "list_files", "args": {}}]},
            {"role": "tool", "results": [{"id": "1", "name": "list_files", "output": "a.py", "is_error": False}]}]

    def test_anthropic(self):
        out = AnthropicClient._convert(self.msgs)
        self.assertEqual([m["role"] for m in out], ["user", "assistant", "user"])
        self.assertEqual(out[2]["content"][0]["type"], "tool_result")

    def test_anthropic_replays_raw_blocks(self):
        raw = [{"type": "thinking", "thinking": "", "signature": "sig"},
               {"type": "tool_use", "id": "1", "name": "list_files", "input": {}}]
        msgs = [dict(m) for m in self.msgs]
        msgs[1]["raw"] = raw
        self.assertEqual(AnthropicClient._convert(msgs)[1]["content"], raw)

    def test_anthropic_response_parsing(self):
        reply = {"content": [{"type": "thinking", "thinking": "", "signature": "s"},
                             {"type": "text", "text": "Reading."},
                             {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a.py"}}],
                 "usage": {"input_tokens": 10, "output_tokens": 5}, "stop_reason": "tool_use"}
        client = AnthropicClient("claude-opus-5-5", api_key="k")
        with mock.patch.object(llm, "_post", return_value=reply) as post:
            r = client.complete("sys", self.msgs, [])
        body = post.call_args[0][2]
        self.assertEqual(body["fallbacks"], "default")
        self.assertNotIn("temperature", body)
        self.assertEqual((r.text, r.tool_calls[0].args, r.raw), ("Reading.", {"path": "a.py"}, reply["content"]))

    def test_anthropic_refusal_raises(self):
        reply = {"content": [], "usage": {"input_tokens": 1, "output_tokens": 0},
                 "stop_reason": "refusal", "stop_details": {"category": "cyber"}}
        with mock.patch.object(llm, "_post", return_value=reply):
            with self.assertRaisesRegex(RuntimeError, "cyber"):
                AnthropicClient("m", api_key="k").complete("sys", self.msgs, [])

    def test_rate_limit_waits_for_retry_after(self):
        import urllib.error
        from email.message import Message
        headers = Message()
        headers["Retry-After"] = "7"
        err = urllib.error.HTTPError("u", 429, "Too Many Requests", headers, io.BytesIO(b"slow down"))
        ok = mock.MagicMock()
        ok.__enter__.return_value.read.return_value = b'{"ok": true}'
        with mock.patch("urllib.request.urlopen", side_effect=[err, ok]), mock.patch("time.sleep") as sleep:
            self.assertEqual(llm._post("https://example.invalid/v1", {}, {}), {"ok": True})
        sleep.assert_called_once_with(7.0)

    def test_openai(self):
        out = OpenAICompatClient._convert("sys", self.msgs)
        self.assertEqual([m["role"] for m in out], ["system", "user", "assistant", "tool"])
        self.assertEqual(out[3]["tool_call_id"], "1")


class TestDotenvAndDemo(unittest.TestCase):
    def test_load_dotenv_does_not_override(self):
        env = Path(tempfile.mkdtemp()) / ".env"
        env.write_text("# comment\nMINI_AGENT_A='from-file'\nexport MINI_AGENT_B=2\nMINI_AGENT_EMPTY=\n")
        with mock.patch.dict(os.environ, {"MINI_AGENT_B": "already-set"}):
            llm.load_dotenv(env)
            self.assertEqual(os.environ["MINI_AGENT_A"], "from-file")
            self.assertEqual(os.environ["MINI_AGENT_B"], "already-set")
            self.assertNotIn("MINI_AGENT_EMPTY", os.environ)
        os.environ.pop("MINI_AGENT_A", None)

    def test_offline_demo_fixes_the_bug(self):
        with redirect_stdout(io.StringIO()) as out:
            code = demo.run_demo(Approver(auto_approve=True), verbose=True)
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("Ran 3 tests", out.getvalue())
        self.assertIn("OK", out.getvalue())


class TestEvalScorer(unittest.TestCase):
    """Checks the eval itself: a real fix scores as fixed, editing the tests does not."""

    def test_correct_fix_counts(self):
        client = ScriptedClient([
            LLMResponse(tool_calls=[call("edit_file", path="shopkit/stats.py",
                                         old_str="(len(values) - 1)", new_str="len(values)")]),
            LLMResponse(text="fixed"),
        ])
        r = run_eval.eval_bug(run_eval.BUGS[0], client, 5, verbose=False, log_root=Path(tempfile.mkdtemp()))
        self.assertTrue(r["fixed"], r)

    def test_every_bug_is_fixable(self):
        # Reference fixes: proves each seeded bug can be fixed without touching tests (a 5/5 "oracle").
        fixes = {
            "stats-average": ("shopkit/stats.py", "(len(values) - 1)", "len(values)"),
            "text-palindrome": ("shopkit/text.py", "return s == s[::-1]",
                                "s = [c.lower() for c in s if c.isalnum()]\n    return s == s[::-1]"),
            "cart-discount": ("shopkit/cart.py", "(1 - discount_percent)", "(1 - discount_percent / 100)"),
            "dates-leap-year": ("shopkit/dates.py", "return year % 4 == 0",
                                "return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)"),
            "inventory-shared-state": ("shopkit/inventory.py", "items=[]):\n        self.items = items",
                                       "items=None):\n        self.items = list(items or [])"),
        }
        self.assertEqual(set(fixes), {b["id"] for b in run_eval.BUGS})
        for bug in run_eval.BUGS:
            path, old, new = fixes[bug["id"]]
            client = ScriptedClient([LLMResponse(tool_calls=[call("edit_file", path=path, old_str=old, new_str=new)]),
                                     LLMResponse(text="fixed")])
            r = run_eval.eval_bug(bug, client, 5, verbose=False, log_root=Path(tempfile.mkdtemp()))
            self.assertTrue(r["fixed"], r)

    def test_summary_table(self):
        d = Path(tempfile.mkdtemp())
        rows = [{"fixed": True, "steps": 4, "tokens": 1000}, {"fixed": False, "steps": 6, "tokens": 3000}]
        meta = {"model": "model-a", "trials": 1, "date": "2026-10-24 10:00"}
        (d / "a.json").write_text(__import__("json").dumps({"meta": meta, "results": rows}))
        table = run_eval.write_summary(d)
        self.assertIn("| model-a | **1/2** (50%) | 5.0 | 2,000 | 1 | 2026-10-24 |", table)

    def test_cheating_does_not_count(self):
        client = ScriptedClient([
            LLMResponse(tool_calls=[call("edit_file", path="tests/test_stats.py",
                                         old_str="average([2, 4, 6]), 4", new_str="average([2, 4, 6]), 6")]),
            LLMResponse(text="fixed"),
        ])
        r = run_eval.eval_bug(run_eval.BUGS[0], client, 5, verbose=False, log_root=Path(tempfile.mkdtemp()))
        self.assertFalse(r["fixed"])
        self.assertFalse(r["tests_untouched"])

    def test_regression_detected(self):
        client = ScriptedClient([
            LLMResponse(tool_calls=[call("edit_file", path="shopkit/text.py",
                                         old_str='return "-".join', new_str='return "_".join')]),
            LLMResponse(text="oops"),
        ])
        r = run_eval.eval_bug(run_eval.BUGS[0], client, 5, verbose=False, log_root=Path(tempfile.mkdtemp()))
        self.assertIn("test_slugify", " ".join(r["regressions"]))


if __name__ == "__main__":
    unittest.main()
