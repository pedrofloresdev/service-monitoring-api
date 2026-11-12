from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str
    FAIL_THRESHOLD: int = 3
    CHECK_TIMEOUT_SECONDS: int = 10


settings = Settings()
