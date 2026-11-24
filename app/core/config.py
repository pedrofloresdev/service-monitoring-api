from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.services.demo_rules import parse_demo_flag


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str
    FAIL_THRESHOLD: int = 3
    CHECK_TIMEOUT_SECONDS: int = 10
    # Bounds the whole name resolution of one host, both address families (spec 002).
    RESOLUTION_TIMEOUT_SECONDS: float = Field(default=2.0, gt=0, le=30)
    # Spec 003a RF-1: read always, strictly. The other demo settings are only read while
    # demo mode is on (app.core.demo_settings), so a malformed one never affects startup.
    DEMO_MODE: str | None = None

    @field_validator("DEMO_MODE")
    @classmethod
    def _check_demo_mode(cls, value: str | None) -> str | None:
        parse_demo_flag(value)  # raises ValueError -> startup fails naming DEMO_MODE
        return value

    @property
    def demo_mode(self) -> bool:
        return parse_demo_flag(self.DEMO_MODE)


settings = Settings()
