from functools import lru_cache

from supabase import Client, create_client

from clinically_anchored_api.core.config import get_settings


@lru_cache
def get_supabase() -> Client:
    """Server-side Supabase client, using the service role key.

    This bypasses row-level security by design -- this service is what's
    responsible for authorization the database's RLS can't express (e.g.
    validating a patient's signed check-in link token). Never expose this
    client or its key to apps/web.
    """
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set. "
            "Copy .env.example to .env and fill in the project's values."
        )
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
