# What's left

Done: harness, tests and CI, offline demo, 60 s demo video (`docs/demo.mp4` / `demo.gif`), README, "What I learned" draft.

1. **Real-model eval (needs an API key).** In the repo: Settings → Secrets and variables → Actions →
   New repository secret `ANTHROPIC_API_KEY`. Then Actions → **eval** → Run workflow (tick *record_demo*).
   Results are committed to `eval/results/`. A free alternative: a Groq or Gemini key as secrets
   `OPENAI_API_KEY` + `OPENAI_BASE_URL`, then run with provider `openai` and a model name.
   Rough cost with the default model: a few dollars per full run; set a spend limit on the key.
2. **After the run:** replace "_Real-model results: run pending._" and the Results section in the README with
   `eval/results/summary.md`, add the last "What I learned" bullet, and optionally re-render the video
   from the real transcript: `python scripts/make_demo_video.py --transcript docs/demo_run.ansi`.
3. **Read "What I learned"** in the README and make it yours; you'll be asked about it in interviews.
4. **Optional narrated video** with [DEMO_SCRIPT.md](DEMO_SCRIPT.md).
5. **Post on LinkedIn**: [LINKEDIN_POST.md](LINKEDIN_POST.md) (upload `docs/demo.mp4` with it).
6. Repo page: add a description, topics (`ai-agents`, `llm`, `python`, `coding-agent`) and pin it on your profile.
