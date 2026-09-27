import os
from contextlib import closing
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.engine import make_url


def create_backup(settings, now=None):
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite":
        raise ValueError("Для PostgreSQL используйте pg_dump. Встроенный backup поддерживает SQLite.")
    source = Path(url.database).resolve()
    if not source.is_file():
        raise ValueError("База данных ещё не создана.")
    now = now or datetime.now(timezone.utc)
    folder = settings.data_dir / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    name = now.strftime("daily-%Y-%m-%d-%H%M%S-%f.sqlite3")
    target = folder / name
    temporary = target.with_suffix(".tmp")
    try:
        with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as src, closing(sqlite3.connect(temporary)) as dst:
            src.backup(dst, pages=128, sleep=0.01)
            dst.execute("PRAGMA journal_mode=DELETE")
            dst.commit()
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity_check failed.")
        os.chmod(temporary, 0o600)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    week = folder / now.strftime("weekly-%G-W%V.sqlite3")
    if not week.exists():
        import shutil

        shutil.copy2(target, week)
    # Keep distinct UTC dates; multiple manual backups on one day do not evict other days.
    daily = sorted(folder.glob("daily-*.sqlite3"), reverse=True)
    seen = set()
    for path in daily:
        day = path.name[6:16]
        if day in seen or len(seen) >= settings.backup_retention_daily:
            if path != target:
                path.unlink()
        else:
            seen.add(day)
    for path in sorted(folder.glob("weekly-*.sqlite3"), reverse=True)[settings.backup_retention_weekly :]:
        path.unlink()
    return target
