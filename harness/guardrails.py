"""Guardrails: folder sandbox, dangerous-command blocklist, human approval."""
from __future__ import annotations

import re
from pathlib import Path

# Each pattern is checked against the whole command string (case-insensitive).
DANGEROUS_PATTERNS = [
    (r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+", "recursive/forced delete (rm -r / rm -f)"),
    (r"\bsudo\b|\bsu\s", "privilege escalation"),
    (r"\bmkfs\b|\bdd\s+if=|>\s*/dev/sd", "disk-level write"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "power control"),
    (r":\(\)\s*\{.*\};\s*:", "fork bomb"),
    (r"\bchmod\s+(-R\s+)?777\b", "world-writable permissions"),
    (r"(curl|wget)[^|]*\|\s*(ba|z)?sh\b", "piping a download into a shell"),
    (r"\bgit\s+push\b.*(--force|-f\b)", "force push"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f)", "destructive git operation"),
    (r"(^|[\s;&|])cd\s+(/|~|\.\.)", "leaving the project folder"),
    (r"\bfind\b.*-delete\b", "bulk delete via find"),
]


class GuardrailError(Exception):
    """Raised when an action is blocked. The message is shown to the model."""


def check_command(cmd: str) -> None:
    for pattern, reason in DANGEROUS_PATTERNS:
        if re.search(pattern, cmd, flags=re.IGNORECASE):
            raise GuardrailError(f"Blocked command ({reason}): {cmd!r}")


def safe_path(root: Path, rel: str) -> Path:
    """Resolve `rel` inside `root`; refuse anything that escapes (../, absolute paths, symlinks)."""
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise GuardrailError(f"Path {rel!r} is outside the project folder")
    return p


class Approver:
    """Asks the human before writes and commands. 'a' = always allow this tool for the session."""

    def __init__(self, auto_approve: bool = False, input_fn=input):
        self.auto_approve = auto_approve
        self.always: set[str] = set()
        self.input_fn = input_fn

    def ask(self, tool: str, preview: str) -> bool:
        if self.auto_approve or tool in self.always:
            return True
        print(f"\n\033[33m── Approval needed: {tool} ──\033[0m\n{preview}")
        ans = self.input_fn("Allow? [y]es / [n]o / [a]lways for this tool: ").strip().lower()
        if ans == "a":
            self.always.add(tool)
            return True
        return ans in ("y", "yes")
