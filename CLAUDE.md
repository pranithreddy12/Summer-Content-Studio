# Builder instructions

- The full spec for this project is `PLAN.md`. Read the relevant section before every task and follow it literally (names, fields, paths, status rules, error codes).
- Work milestone by milestone (PLAN.md §17). Don't start the next milestone until `python -m pytest` is green and the app starts.
- After each milestone append a summary to `docs/PROGRESS.md` (files changed, features, real test results, known issues, next step).
- Use the venv: `.venv\Scripts\python` (Windows). Python 3.13.
- Never call the real Anthropic API from tests. Never commit `.env` or any key.
- Business logic goes in `app/services/` only; API and dashboard routes stay thin.
- No new dependencies, frameworks, or Phase 2/3 features (PLAN.md §21). Record any unavoidable deviation in `docs/DECISIONS.md`.
- Never write descriptions of images you haven't seen; never fake publishing.
