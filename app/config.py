from pathlib import Path
from zoneinfo import ZoneInfo
from pydantic import SecretStr, field_validator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_model: str = "openrouter/free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_seconds: int = Field(default=60, ge=1, le=180)
    openrouter_http_referer: str = ""
    openrouter_app_name: str = "Food Planner"
    ai_user_daily_limit: int = Field(default=5, ge=1)
    ai_user_cooldown_seconds: int = Field(default=60, ge=0)
    bot_token: SecretStr = SecretStr("")
    admin_ids: str = ""
    database_url: str = "sqlite+aiosqlite:///data/database/food_planner.db"
    timezone: str = "Europe/Berlin"
    log_level: str = "INFO"
    app_env: str = "production"
    data_dir: Path = Path("data")
    backup_enabled: bool = True
    backup_retention_daily: int = Field(default=7, ge=1, le=365)
    backup_retention_weekly: int = Field(default=4, ge=1, le=52)
    max_import_mb: int = Field(default=5, ge=1, le=20)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        ZoneInfo(value)
        return value

    @field_validator("admin_ids")
    @classmethod
    def valid_admins(cls, value):
        for item in value.split(","):
            if item.strip() and (not item.strip().isdigit() or int(item.strip()) <= 0):
                raise ValueError("ADMIN_IDS must contain positive Telegram IDs separated by commas")
        return value

    @property
    def admins(self):
        return {int(x.strip()) for x in self.admin_ids.split(",") if x.strip()}

    def validate_token(self):
        token = self.bot_token.get_secret_value()
        if not token or token.startswith("PUT_"):
            raise ValueError("BOT_TOKEN is missing. Configure .env before starting the bot.")
        return token

    def directories(self):
        for name in ("database", "exports", "imports", "backups"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)
        Path("logs").mkdir(exist_ok=True)
