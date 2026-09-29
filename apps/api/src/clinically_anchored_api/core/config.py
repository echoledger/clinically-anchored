import logging
import secrets
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


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

    # Signs patient link tokens (itsdangerous). Left empty in development it
    # becomes a random per-process secret (links die on restart, which is fine
    # locally). Any other environment must set a real, stable value via
    # Railway -- otherwise every issued link breaks on restart, and differs
    # between instances. Enforced by `_require_secrets_outside_development`.
    checkin_link_secret: str = ""
    checkin_link_max_age_seconds: int = 60 * 60 * 24 * 14  # 14 days
    # Message links expose a whole conversation, so they're separate tokens
    # (scope=messages) with a much shorter life than check-in links.
    message_link_max_age_seconds: int = 60 * 60 * 24 * 2  # 48 hours

    # Ed25519 signing key for the audit log: base64 of the 32-byte private seed.
    # Must be stable -- a regenerated key orphans every row already signed.
    # Generate with `python -m clinically_anchored_api.core.audit`. Deployed
    # environments set it via Railway; move to a KMS before real patient data.
    audit_signing_key: str = ""
    audit_key_id: str = "dev-1"

    # CORS: the web app's origin(s), comma-separated in the env var
    allowed_origins: str = "http://localhost:3000"

    @model_validator(mode="after")
    def _require_secrets_outside_development(self) -> "Settings":
        if self.environment == "development":
            if not self.checkin_link_secret:
                self.checkin_link_secret = secrets.token_urlsafe(32)
                logger.warning(
                    "CHECKIN_LINK_SECRET not set: using a random per-process secret "
                    "(issued links will stop working on restart)."
                )
            return self
        required = {
            "CHECKIN_LINK_SECRET": self.checkin_link_secret,
            "AUDIT_SIGNING_KEY": self.audit_signing_key,
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_SERVICE_ROLE_KEY": self.supabase_service_role_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(
                f"ENVIRONMENT={self.environment!r} requires these to be set: {', '.join(missing)}"
            )
        return self

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
