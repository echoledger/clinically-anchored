import secrets
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment variables / .env.

    Nothing here should ever hold a real secret value by default -- only
    names and safe defaults. Real values come from Railway's environment
    variable store in deployed environments.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"

    # Supabase (ca-central-1 project)
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # Signs patient check-in link tokens (itsdangerous). Dev default is random
    # per-process on purpose -- it should never be reused as a real secret;
    # deployed environments must set a real, stable value via Railway.
    checkin_link_secret: str = secrets.token_urlsafe(32)
    checkin_link_max_age_seconds: int = 60 * 60 * 24 * 14  # 14 days

    # Ed25519 signing key for the audit log: base64 of the 32-byte private seed.
    # Must be stable -- a regenerated key orphans every row already signed.
    # Generate with `python -m clinically_anchored_api.core.audit`. Deployed
    # environments set it via Railway; move to a KMS before real patient data.
    audit_signing_key: str = ""
    audit_key_id: str = "dev-1"

    # CORS: the web app's origin(s), comma-separated in the env var
    allowed_origins: str = "http://localhost:3000"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
