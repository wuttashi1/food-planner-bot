import sqlite3
from datetime import datetime, timezone, timedelta
from app.config import Settings
from app.services.backup import create_backup
from app.bot.ui import Action, keyboard, button
import pytest


def test_token_missing():
    with pytest.raises(ValueError, match="BOT_TOKEN is missing"):
        Settings(bot_token="", _env_file=None).validate_token()


def test_safe_backup_and_retention(tmp_path):
    source = tmp_path / "db.sqlite3"
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.execute("INSERT INTO test VALUES (42)")
        conn.commit()
        settings = Settings(
            database_url="sqlite+aiosqlite:///" + str(source),
            data_dir=tmp_path,
            backup_retention_daily=2,
            backup_retention_weekly=1,
            _env_file=None,
        )
        for i in range(5):
            path = create_backup(settings, datetime(2026, 9, 14, tzinfo=timezone.utc) + timedelta(days=i))
        with sqlite3.connect(path) as backup:
            assert backup.execute("SELECT id FROM test").fetchone()[0] == 42
    assert len(list((tmp_path / "backups").glob("daily-*"))) == 2
    assert len(list((tmp_path / "backups").glob("weekly-*"))) == 1


def test_callback_size():
    examples = [
        Action(a="buy", i=10000, p=100, d="2026-09-18,6,deadbeef", h=1000000),
        Action(a="setrole", i=999999999999, h=1000000),
        Action(a="confirmimport", i=2, h=1000000),
    ]
    assert all(len(x.pack().encode()) <= 64 for x in examples)
    assert keyboard([[button("Меню", "home")]], 1).inline_keyboard


def test_compose_configuration():
    from pathlib import Path
    import yaml

    config = yaml.safe_load(Path("docker-compose.yml").read_text())
    bot = config["services"]["bot"]
    assert bot["restart"] == "unless-stopped"
    assert bot["env_file"] == [".env"]
    assert set(bot["volumes"]) == {"./data:/app/data", "./logs:/app/logs"}
    assert not bot.get("ports")
    assert bot["read_only"] is True
    dockerfile = Path("Dockerfile").read_text()
    assert "USER 10001:10001" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    entry = Path("scripts/entrypoint.sh").read_text()
    assert entry.index("alembic upgrade head") < entry.index("exec python main.py")


def test_source_zip_excludes_secrets_and_runtime_data(tmp_path):
    from scripts.package import package
    import zipfile

    path = package(tmp_path / "deployment.zip")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert ".env.example" in names
        assert ".env" not in names
        assert "data/.gitkeep" in names
        assert not any(n.startswith(".venv") or "__pycache__" in n or n.endswith((".db", ".sqlite3", ".log", ".jsonl")) for n in names)
        assert "app/templates/excel/food_planner_template.xlsx" in names
