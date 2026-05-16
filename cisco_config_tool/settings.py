from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    data_dir: Path = Field(default=Path("data"))
    database_path: Path | None = None
    secret_key_file: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8088
    openai_api_key: str = ""
    openai_model: str = "gpt-5.2"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CISCO_TOOL_",
        extra="ignore",
    )

    @property
    def resolved_data_dir(self) -> Path:
        return self.data_dir.resolve()

    @property
    def resolved_database_path(self) -> Path:
        if self.database_path is not None:
            return self.database_path.resolve()
        return self.resolved_data_dir / "cisco_config_tool.sqlite3"

    @property
    def resolved_secret_key_file(self) -> Path:
        if self.secret_key_file is not None:
            return self.secret_key_file.resolve()
        return self.resolved_data_dir / "secret.key"

    @property
    def resolved_session_log_dir(self) -> Path:
        return self.resolved_data_dir / "session_logs"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
