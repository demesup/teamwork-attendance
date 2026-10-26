from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./attendance.db"
    jwt_secret: str = "development-jwt-secret-change-me"
    code_secret: str = "development-code-secret-change-me"
    access_token_minutes: int = 30
    qr_ttl_seconds: int = 30
    attempt_window_minutes: int = 10
    attempt_limit_per_student: int = 5
    attempt_limit_per_ip: int = 30
    attempt_limit_per_device: int = 30
    login_window_minutes: int = 15
    login_attempt_limit: int = 10
    # Professors register with this shared code (given out by the department). Empty disables professor sign-up.
    professor_signup_code: str = ""
    upload_dir: str = "./uploads"
    max_upload_bytes: int = 5 * 1024 * 1024
    # "production" refuses to start with the built-in development secrets.
    app_env: str = "development"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
