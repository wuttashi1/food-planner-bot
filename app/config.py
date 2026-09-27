from pathlib import Path
from zoneinfo import ZoneInfo
from pydantic import SecretStr, field_validator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
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
