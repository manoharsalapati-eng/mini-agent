"""The four core tools: read_file, list_files, edit_file, run_command."""
from __future__ import annotations

import difflib
import subprocess
from pathlib import Path

from .guardrails import Approver, GuardrailError, check_command, safe_path

MAX_OUTPUT = 10_000  # chars returned to the model per tool call
IGNORED_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".agent_logs", ".pytest_cache"}

TOOL_SCHEMAS = [
    {
        "name": "read_file",
        "description": "Read a text file in the project. Returns contents with line numbers.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path relative to the project root"}},
            "required": ["path"]},
    },
    {
        "name": "list_files",
        "description": "List files recursively under a directory (default: project root). Skips .git, venvs, caches.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Directory relative to project root", "default": "."}}},
    },
    {
        "name": "edit_file",
        "description": ("Replace an exact snippet in a file. old_str must appear exactly once — include surrounding "
                        "lines to make it unique. To create a new file, pass old_str as an empty string."),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_str": {"type": "string", "description": "Exact text to replace ('' to create a new file)"},
            "new_str": {"type": "string", "description": "Replacement text"}},
            "required": ["path", "old_str", "new_str"]},
    },
    {
        "name": "run_command",
        "description": "Run a shell command in the project root (e.g. tests). 60 s timeout. Returns exit code + output.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}}, "required": ["command"]},
    },
]


def _clip(s: str) -> str:
    return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + f"\n…[truncated {len(s) - MAX_OUTPUT} chars]"


class Toolbox:
    def __init__(self, root: Path, approver: Approver, timeout: int = 60):
        self.root = root.resolve()
        self.approver = approver
        self.timeout = timeout

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        """Returns (output, is_error). Errors are returned to the model, never raised."""
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return f"Unknown tool: {name}", True
        if "_raw" in args:
            return f"Could not parse tool arguments as JSON: {args['_raw'][:200]}", True
        try:
            return _clip(fn(**args)), False
        except GuardrailError as e:
            return str(e), True
        except TypeError as e:
            return f"Bad arguments for {name}: {e}", True
        except Exception as e:  # noqa: BLE001 — anything else goes back to the model
            return f"{type(e).__name__}: {e}", True

    # ---- tools ----
    def t_read_file(self, path: str) -> str:
        p = safe_path(self.root, path)
        if not p.is_file():
            raise FileNotFoundError(f"No such file: {path}")
        lines = p.read_text(errors="replace").splitlines()
        return "\n".join(f"{i:4d}| {line}" for i, line in enumerate(lines, 1)) or "(empty file)"

    def t_list_files(self, path: str = ".") -> str:
        base = safe_path(self.root, path)
        if not base.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")
        files = []
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(self.root)
            if any(part in IGNORED_DIRS for part in rel.parts) or not p.is_file():
                continue
            files.append(str(rel))
            if len(files) >= 500:
                files.append("…(stopped at 500 files)")
                break
        return "\n".join(files) or "(no files)"

    def t_edit_file(self, path: str, old_str: str, new_str: str) -> str:
        p = safe_path(self.root, path)
        if old_str == "":
            if p.exists():
                raise FileExistsError(f"{path} exists; give old_str to edit it")
            before, after = "", new_str
        else:
            if not p.is_file():
                raise FileNotFoundError(f"No such file: {path}")
            before = p.read_text()
            n = before.count(old_str)
            if n == 0:
                raise ValueError("old_str not found — re-read the file and copy the text exactly (whitespace matters)")
            if n > 1:
                raise ValueError(f"old_str appears {n} times — add surrounding lines to make it unique")
            after = before.replace(old_str, new_str, 1)
        diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                            fromfile=f"a/{path}", tofile=f"b/{path}"))
        if not self.approver.ask("edit_file", diff or "(no change)"):
            return "User denied this edit. Ask what they want or try a different approach."
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(after)
        return f"{'Created' if old_str == '' else 'Edited'} {path}\n{diff}"

    def t_run_command(self, command: str) -> str:
        check_command(command)
        if not self.approver.ask("run_command", f"$ {command}"):
            return "User denied this command."
        try:
            r = subprocess.run(command, shell=True, cwd=self.root, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return f"Command timed out after {self.timeout}s"
        return f"exit code: {r.returncode}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}"
