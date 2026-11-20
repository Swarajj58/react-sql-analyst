from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    groq_api_key: str = Field(..., description="Groq API key")
    llm_model: str = Field(default="openai/gpt-oss-20b")
    db_path: Path = Field(default=Path("data/analytics.db"))
    logs_dir: Path = Field(default=Path("logs"))

    # Agent Loop Budgets & Guardrails
    max_steps: int = Field(default=8, ge=1, le=20)
    max_consecutive_errors: int = Field(default=3, ge=1, le=10)
    llm_temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    llm_timeout_sec: float = Field(default=30.0)

    # Context Protection Limits
    max_rows_returned: int = Field(default=50, ge=1, le=500)
    max_observation_chars: int = Field(default=2500, ge=500, le=20000)
    sql_timeout_sec: float = Field(default=5.0)


def get_settings() -> Settings:
    return Settings()