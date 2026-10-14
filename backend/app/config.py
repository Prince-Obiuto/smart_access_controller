from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class ControllerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )
    controller_mode: Literal["SIMULATED", "HIKVISION_CONTROLLED"] = "SIMULATED"
    hikvision_host: str = "10.10.20.8"
    hikvision_port: int = Field(default=8000, ge=1, le=65535)
    hikvision_username: SecretStr = SecretStr("")
    hikvision_password: SecretStr = SecretStr("")
    hikvision_sdk_dir: Path = ROOT / "backend/var/hikvision-sdk"
    hikvision_connect_timeout: float = Field(default=3, ge=0.1, le=10)
    hikvision_bridge_path: Path = ROOT / "backend/var/hikvision/ift-hikvision"
    hikvision_process_timeout: float = Field(default=40, ge=5, le=120)
    hikvision_initial_minutes: int = Field(default=60, ge=2, le=129600)
    hikvision_history_window_minutes: int = Field(default=15, ge=1, le=60)
    hikvision_event_limit: int = Field(default=1000, ge=1, le=10000)
    hikvision_controller_id: str = Field(default="controller-1", max_length=64)

    @model_validator(mode="after")
    def sdk_path(self):
        if not self.hikvision_sdk_dir.is_absolute():
            self.hikvision_sdk_dir = (ROOT / self.hikvision_sdk_dir).resolve()
        if not self.hikvision_bridge_path.is_absolute():
            self.hikvision_bridge_path = (ROOT / self.hikvision_bridge_path).resolve()
        return self

    @property
    def controller_writes_available(self):
        return self.controller_mode in {"SIMULATED", "HIKVISION_CONTROLLED"}


class Settings(ControllerSettings):
    database_url: str = Field(repr=False)
    environment: str = "development"
    allow_insecure_dev_cookies: bool = False
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    upload_dir: Path = ROOT / "backend/var/uploads"
    upload_limit: int = 5 * 1024 * 1024
    session_hours: int = 12
    simulation_controls_enabled: bool = False
    jobs_enabled: bool = True
    permissions_path: Path = ROOT / "shared/permissions.json"
    application_url: str = "http://localhost:5173"
    recovery_contact: str = "Contact the IFT E-Library administrator in person."
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: SecretStr = SecretStr("")
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    smtp_security: Literal["STARTTLS", "TLS", "PLAIN"] = "STARTTLS"

    @model_validator(mode="after")
    def validate_runtime(self):
        from urllib.parse import urlsplit

        url = urlsplit(self.application_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("APPLICATION_URL must be a trusted application origin.")
        if self.environment != "development" and (
            url.scheme != "https" or self.smtp_security == "PLAIN"
        ):
            raise ValueError(
                "Recovery requires HTTPS and encrypted SMTP outside development."
            )
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("PostgreSQL with psycopg is required.")
        if self.environment != "development" and self.allow_insecure_dev_cookies:
            raise ValueError(
                "Insecure cookies are only allowed in explicit development mode."
            )
        if not 1024 <= self.upload_limit <= 20 * 1024 * 1024:
            raise ValueError("Upload limit must be between 1 KiB and 20 MiB.")
        if not self.upload_dir.is_absolute():
            self.upload_dir = (ROOT / self.upload_dir).resolve()
        if not 1 <= self.session_hours <= 168:
            raise ValueError("Session lifetime must be 1 to 168 hours.")
        return self

    @property
    def secure_cookie(self):
        return not self.allow_insecure_dev_cookies

    @property
    def origins(self):
        return {o.strip().rstrip("/") for o in self.allowed_origins.split(",")}
