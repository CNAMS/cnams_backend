"""Central settings — env only, fail-fast. Roadmap §7."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./cnams.db"
    jwt_secret: str = ""  # required, fail-fast
    admin_enrolment_code: str = ""
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    reference_table_version: str = "v1.0.0-lms"
    engine_version: str = "1.0.0-lms"
    app_name: str = "growth-monitor-api"
    name_enc_key: str = ""  # Fernet key (base64). Empty = dev plaintext w/ marker.

    def validate_required(self) -> None:
        missing: list[str] = []
        if not self.jwt_secret or len(self.jwt_secret) < 32:
            missing.append("JWT_SECRET (min 32 chars)")
        if not self.admin_enrolment_code:
            missing.append("ADMIN_ENROLMENT_CODE")
        if missing:
            raise RuntimeError(f"Missing required env: {', '.join(missing)}")


settings = Settings()
