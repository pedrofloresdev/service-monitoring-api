from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str
    FAIL_THRESHOLD: int = 3
    CHECK_TIMEOUT_SECONDS: int = 10
    # Bounds the whole name resolution of one host, both address families (spec 002).
    RESOLUTION_TIMEOUT_SECONDS: float = Field(default=2.0, gt=0, le=30)


settings = Settings()
