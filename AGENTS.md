# Notes for agents working on this repo
- Pure stdlib Python 3.10+. Do not add dependencies.
- Run tests: `python -m unittest discover -s tests -v`
- Smoke test without an API key: `python -m harness --demo --yes`
- Do not edit files in eval/sample_repo/ — the bugs there are intentional.
- Never commit `.env` or API keys.
