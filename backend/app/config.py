from pathlib import Path
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )
    database_url: str
    environment: str = "development"
    allow_insecure_dev_cookies: bool = False
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    upload_dir: Path = ROOT / "backend/var/uploads"
    upload_limit: int = 5 * 1024 * 1024
    session_hours: int = 12
    controller_mode: str = "SIMULATED"
    simulation_controls_enabled: bool = False
    jobs_enabled: bool = True
    permissions_path: Path = ROOT / "shared/permissions.json"

    @model_validator(mode="after")
    def validate_runtime(self):
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("PostgreSQL with psycopg is required.")
        if self.environment != "development" and self.allow_insecure_dev_cookies:
            raise ValueError(
                "Insecure cookies are only allowed in explicit development mode."
            )
        if self.controller_mode != "SIMULATED":
            raise ValueError(
                "Only the simulated adapter is implemented; real hardware is unverified."
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
