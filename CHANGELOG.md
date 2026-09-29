# Changelog

## [1.0.0] - 2026-09-30

### Added
- Async OpenRouter integration using `openrouter/free`, JSON Schema and local Pydantic validation.
- Telegram AI wizard, parameter summaries, preview and confirmation, plus regeneration of a whole menu, day or single meal.
- Conservative multilingual ingredient resolution with aliases and one automatic repair attempt.
- Local nutrition calculations and deterministic portion scaling using the existing calculation engine.
- Russian, Ukrainian, English and German translations for the AI interface, with persisted language preferences.
- Persistent daily limits, cooldowns, generation history and localized API errors.
- Alembic migration `4c10_ai_menu` adding `ai_usage` and `ingredient_aliases`.
- Mocked AI tests with an automatic guard against real OpenRouter HTTP calls.

### Changed
- AI menus save transactionally through existing versioning, household permissions and shopping lists.
- AI requests execute outside the shared SQLite write lock.
- Internal language and AI preferences are excluded from legacy Excel SETTINGS exports.
- README and Russian guide document AI configuration and manual verification.

### Upgrade
- Preserve existing `.env`, `data/` and `logs/`; set `OPENROUTER_API_KEY` to enable AI.
- Run `alembic upgrade head`; Docker applies migrations automatically at startup.
- Ordinary features remain available without an OpenRouter key.

### Known limitations
- Legacy Telegram screens remain Russian; four-language support covers the new AI interface.
- An imported ingredient catalog or demo menu is required before generation.
- Budget is a preference, not a calculated price. Unknown restrictions are rejected; allergen information for arbitrary composite foods is not inferred.
- Unsaved previews expire on restart. Periods start on Monday and previews show base portions before household overrides.

### Validation
- 124 tests passed; Ruff checks passed; no real OpenRouter generation requests during tests.
- Fresh and existing SQLite migrations and startup without an AI key verified.
- Docker image built on the deployment server; deployed container reported healthy with zero restarts.
- No live OpenRouter generation test was run.

[1.0.0]: https://github.com/wuttashi1/food-planner-bot/releases/tag/v1.0.0
