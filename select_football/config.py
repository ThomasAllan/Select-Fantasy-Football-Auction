from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Data
    data_dir: Path = Path("data")

    # FPL
    fpl_base_url: str = "https://fantasy.premierleague.com/api"

    # Email
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    send_test_only: bool = False
    test_email: str = ""
    # Recipient list for the monthly report. Set via the MANAGER_EMAILS secret /
    # env var. Accepts the "name,email" CSV text used by the GitHub secret, or a
    # plain list of addresses separated by commas / semicolons / newlines.
    manager_emails: str = ""

    # Logging
    log_level: str = "INFO"

    @field_validator("data_dir", mode="before")
    @classmethod
    def resolve_data_dir(cls, v: str | Path) -> Path:
        return Path(v).resolve()

    @field_validator("smtp_port", mode="before")
    @classmethod
    def default_smtp_port(cls, v: str | int) -> str | int:
        # An empty env var / secret (SMTP_PORT="") should fall back to the default,
        # not blow up int parsing.
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return 587
        return v


def get_settings() -> Settings:
    return Settings()
