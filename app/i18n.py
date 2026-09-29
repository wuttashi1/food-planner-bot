import json
from pathlib import Path

LOCALES = ("ru", "uk", "en", "de")
CATALOGS = {locale: json.loads((Path(__file__).parent / "locales" / f"{locale}.json").read_text(encoding="utf-8")) for locale in LOCALES}


def locale_for(code: str | None) -> str:
    value = (code or "").lower().split("-")[0]
    return value if value in LOCALES else "en"


def t(key: str, locale: str = "en", **values) -> str:
    return CATALOGS[locale_for(locale)].get(key, CATALOGS["en"].get(key, key)).format(**values)
