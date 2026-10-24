# LinkedIn post draft

Attach `docs/demo.mp4` as the post's video (native video gets more reach than a link). Fill in the eval line after the real-model run, or drop it until then; keep it in your own voice.

---

I built a coding agent from scratch to understand what tools like Claude Code and Cursor actually do. 🛠️

mini-agent is ~600 lines of Python with zero dependencies. You give it a folder and a bug report; it explores the code, edits files and runs the tests until it's done.

What's inside the "harness":
🔁 An agent loop: model → tool calls → results → model, with a 25-step limit
🧰 4 tools: read_file, list_files, edit_file (exact-match replace with a diff), run_command
🛡️ Guardrails: folder sandbox (symlink-safe), dangerous-command blocklist, human approval before any write or command
🧠 Memory: trims old tool output, then summarises when the context gets long; loads AGENTS.md project notes
📜 Logging: every step written to JSONL for debugging

To measure it, I seeded 5 bugs in a sample repo. A fix only counts if the tests pass, the test files are unchanged and nothing else breaks.
Result: X/5 fixed with <model>, ~N steps per bug.

What surprised me: <one honest sentence, e.g. "clear error messages from the tools mattered as much as the model">

Code + eval: https://github.com/manoharsalapati-eng/mini-agent

#AI #LLM #Agents #Python #BuildInPublic
