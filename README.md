![Food Planner](assets/banner.svg)

<div align="center">

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white) ![aiogram](https://img.shields.io/badge/aiogram-3-26A5E4?logo=telegram&logoColor=white) ![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white) ![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)

**A Telegram companion for personal and shared weekly meal planning.**

[English](README.md) · [Русский](README.ru.md) · [Detailed guide / Подробное руководство](GUIDE.ru.md)

[![Open in Telegram](https://img.shields.io/badge/Open_in_Telegram-Открыть_бота-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/wutshyfoodplanner_bot)

</div>

## Highlights

Latest release: [v1.0.0](https://github.com/wuttashi1/food-planner-bot/releases/tag/v1.0.0) · [Changelog](CHANGELOG.md)

- Weekly menus, recipes, ingredient-based calories and macros.
- Shared households, personal portions and collaborative shopping lists.
- Excel import with preview and confirmation, export and menu version history.
- Meal reminders, weight tracking, menu sharing and scheduled backups.

## Quick start

```bash
git clone https://github.com/wuttashi1/food-planner-bot.git
cd food-planner-bot
cp .env.example .env
# Set BOT_TOKEN in .env; optionally set ADMIN_IDS.
mkdir -p data logs
sudo chown -R 10001:10001 data logs
chmod 600 .env
docker compose up -d --build
```

The container applies Alembic migrations at startup. Keep `data/` and `logs/` persistent. Long polling requires no public domain or inbound port. The bot interface is in Russian.

## Development checks

```bash
python -m pip install -r requirements.lock -r requirements-dev.txt
python -m pytest -q
```

## Project map

`app/bot/` · Telegram UI | `app/services/` · planning & Excel | `app/database/` · storage | `migrations/` · schema | `tests/` · checks

## Documentation

- [ Full operating guide: configuration, workflows and limitations (Russian) ](GUIDE.ru.md)

Keep credentials in your local `.env`. Runtime databases and backups are excluded from version control.

## AI Menu Generation

Use `/ai` or **✨ AI Menu** on the home screen. Import your ingredient catalog
using the existing Excel flow (or load the demo) before generating a menu.

Telegram → structured request → OpenRouter → Pydantic validation → ingredient
resolver → local nutrition calculation → preview → confirmation → SQLite menu
version → existing shopping list.

AI chooses dishes and menu structure. Calories and macronutrients come exclusively
from the existing Food Planner calculation engine and ingredient catalog, never
from the LLM. Catalog nutrition uses the existing per-100 g/ml/pcs convention.
Prices are not stored: a EUR budget is a preference, not an estimated bill.

The AI wizard supports Russian (`ru`), Ukrainian (`uk`), English (`en`), and German
(`de`). Select **Settings → Language**; the initial preference comes from Telegram,
with English as fallback. Existing legacy screens remain Russian. Locale and AI
preferences are stored in user settings in SQLite and excluded from legacy Excel
SETTINGS exports for compatibility.

Set these environment variables (all defaults are in `.env.example`):

```dotenv
OPENROUTER_API_KEY=
OPENROUTER_MODEL=openrouter/free
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_TIMEOUT_SECONDS=60
OPENROUTER_HTTP_REFERER=
OPENROUTER_APP_NAME=Food Planner
AI_USER_DAILY_LIMIT=5
AI_USER_COOLDOWN_SECONDS=60
```

Without an API key the bot starts normally and only AI generation is unavailable.
Run `alembic upgrade head` before starting; the Docker entrypoint already does so.
Migration `4c10_ai_menu` adds persistent usage history and ingredient aliases.
Daily limits reset at midnight UTC, survive restarts, and exclude `ADMIN_IDS`.
A generation, including its single possible repair, consumes one attempt; local
validation failures consume none. Transient 429/502/503/504 responses get one
short retry. Network errors and timeouts are not retried automatically.

The OpenRouter request uses JSON Schema and requires supporting endpoints:
[structured-output documentation](https://openrouter.ai/docs/guides/features/structured-outputs).
Unavailable compatible models produce a localized error without changing the menu.
Only food preferences and a bounded food/recipe catalog are sent, without personal
identifiers. The API key and full prompts are never logged.

Menus occupy existing weekday slots starting Monday. Previews show one person's
base portion; existing personal portions, disabled meals and household overrides
remain applicable after saving. Save uses the existing version transaction and
revision check. Unsaved previews are held in FSM memory and expire on restart;
compact generation history remains in SQLite. Ingredient aliases refer to versioned
ingredients; stable external IDs remain unchanged across menu versions.
Unknown or ambiguous ingredients get one repair attempt. Unrecognized restrictions
are rejected: use precise catalog names. The alias vocabulary is intentionally
conservative and cannot establish complete allergen information for arbitrary
imported composite foods. Review ingredient labels where allergies matter.

### Testing AI changes

```sh
pytest -q
```

Normal tests use mocked HTTP responses, make **zero real OpenRouter requests** and
block accidental OpenRouter HTTP calls. No live API smoke test is included or run.

Manual check: configure the key, restart, import a catalog, open `/ai`, choose
parameters, review the summary, generate, browse preview days, try replacing one
dish, then confirm Save. Verify the active menu and household shopping list. Repeat
with the key unset to verify the localized configuration error.
