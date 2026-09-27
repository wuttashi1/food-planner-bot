![Food Planner](assets/banner.svg)

<div align="center">

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white) ![aiogram](https://img.shields.io/badge/aiogram-3-26A5E4?logo=telegram&logoColor=white) ![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white) ![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)

**Telegram-бот для личного и совместного планирования питания.**

[English](README.md) · [Русский](README.ru.md) · [Detailed guide / Подробное руководство](GUIDE.ru.md)

[![Open in Telegram](https://img.shields.io/badge/Open_in_Telegram-Открыть_бота-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/wutshyfoodplanner_bot)

</div>

## Возможности

- Недельное меню, рецепты, расчёт калорий и БЖУ по ингредиентам.
- Совместный рацион, личные порции и общие списки покупок.
- Импорт Excel с предпросмотром, экспорт и история версий меню.
- Напоминания, история веса, обмен меню и резервные копии.

## Быстрый запуск

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

Контейнер применяет миграции Alembic при запуске. Данные хранятся в `data/`, логи — в `logs/`. Благодаря long polling домен и входящие порты не нужны. Интерфейс бота — на русском.

## Проверка проекта

```bash
python -m pip install -r requirements.lock -r requirements-dev.txt
python -m pytest -q
```

## Структура

`app/bot/` · Telegram UI | `app/services/` · planning & Excel | `app/database/` · storage | `migrations/` · schema | `tests/` · checks

## Документация

- [ Полное руководство: настройка, работа и ограничения ](GUIDE.ru.md)

Токен храните только в локальном `.env`. Базы данных и резервные копии не предназначены для публикации.
