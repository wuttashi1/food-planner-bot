"""Build a source-only deployment ZIP; never include local user data or secrets."""

from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "main.py",
    "requirements.txt",
    "requirements.lock",
    "requirements-dev.txt",
    "Dockerfile",
    "docker-compose.yml",
    ".env.example",
    ".gitignore",
    ".dockerignore",
    "README.md",
    "CHANGELOG.md",
    "README.ru.md",
    "GUIDE.ru.md",
    ".gitattributes",
    "alembic.ini",
    "pyproject.toml",
    "data/.gitkeep",
    "logs/.gitkeep",
]
FOLDERS = ["app", "tests", "migrations", "scripts", ".github", "assets"]


def package(destination=None):
    target = destination or ROOT / "food-planner-bot.zip"
    paths = [ROOT / f for f in FILES]
    for folder in FOLDERS:
        paths.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, path.relative_to(ROOT))
    print(target)
    return target


if __name__ == "__main__":
    package()
