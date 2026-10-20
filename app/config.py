from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./attendance.db"
    jwt_secret: str = "development-jwt-secret-change-me"
    code_secret: str = "development-code-secret-change-me"
    access_token_minutes: int = 30
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    seed_demo_data: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
