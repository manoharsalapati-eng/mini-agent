# What's left (needs your API key / your voice)

1. **Run the eval.** `cp .env.example .env`, add `ANTHROPIC_API_KEY`, then `python eval/run_eval.py --trials 3`.
   Or add the key as a GitHub secret and run **Actions → eval**. Optionally compare a second model
   (`--provider openai` with Groq/Gemini) for a two-row table.
2. **Fill the README.** The "Eval:" line near the top (X/5, model, mean steps, tokens) and the Results
   table: paste `eval/results.md`. Commit `eval/results.md` and `eval/results.json`.
3. **Record the demo video** with [DEMO_SCRIPT.md](DEMO_SCRIPT.md) and put the link on the 🎥 line.
4. **Write "What I learned"** in your own words from what you saw in the eval logs (`eval/logs/`).
5. **Post on LinkedIn**: [LINKEDIN_POST.md](LINKEDIN_POST.md).
6. Repo settings: add a description, topics (`ai-agents`, `llm`, `python`, `coding-agent`) and pin it on your profile.
