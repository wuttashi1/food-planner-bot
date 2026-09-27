![Food Planner](assets/banner.svg)

<div align="center">

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white) ![aiogram](https://img.shields.io/badge/aiogram-3-26A5E4?logo=telegram&logoColor=white) ![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white) ![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)

**A Telegram companion for personal and shared weekly meal planning.**

[English](README.md) · [Русский](README.ru.md) · [Detailed guide / Подробное руководство](GUIDE.ru.md)

[![Open in Telegram](https://img.shields.io/badge/Open_in_Telegram-Открыть_бота-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/wutshyfoodplanner_bot)

</div>

## Highlights

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
