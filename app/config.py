from zoneinfo import ZoneInfo
from datetime import date, datetime

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://salesparser:salesparser@localhost:5432/salesparser"
    # Расписание ежедневного обхода (crontab) и часовой пояс для него и для даты снапшота
    scrape_cron: str = "0 3 * * *"
    timezone: str = "Europe/Moscow"
    run_on_start: bool = False
    admin_token: str = "change-me"
    sources_dir: str = "sources"
    # Встроенные демо-магазины (/demo/a|b|c) для проверки конвейера без реальных сайтов
    demo_enabled: bool = True
    # Цена считается актуальной, если снапшот не старше N дней
    stale_days: int = 2
    max_parallel_sources: int = 4
    queue_poll_seconds: int = 15
    user_agent: str = "Mozilla/5.0 (compatible; SalesParserBot/1.0)"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def today(self) -> date:
        return datetime.now(self.tz).date()


settings = Settings()
