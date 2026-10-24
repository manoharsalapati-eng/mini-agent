"""Offline tests for the harness — no API key needed (uses ScriptedClient)."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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


class TestWireFormats(unittest.TestCase):
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok", "tool_calls": [{"id": "1", "name": "list_files", "args": {}}]},
            {"role": "tool", "results": [{"id": "1", "name": "list_files", "output": "a.py", "is_error": False}]}]

    def test_anthropic(self):
        out = AnthropicClient._convert(self.msgs)
        self.assertEqual([m["role"] for m in out], ["user", "assistant", "user"])
        self.assertEqual(out[2]["content"][0]["type"], "tool_result")

    def test_openai(self):
        out = OpenAICompatClient._convert("sys", self.msgs)
        self.assertEqual([m["role"] for m in out], ["system", "user", "assistant", "tool"])
        self.assertEqual(out[3]["tool_call_id"], "1")


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
