from app.config import Settings

try:
    settings = Settings()
    settings.validate_token()
    settings.directories()
except Exception as exc:
    message = (
        str(exc)
        if str(exc).startswith("BOT_TOKEN is missing")
        else "Invalid environment configuration. Check .env values and directory permissions."
    )
    raise SystemExit(message) from None
