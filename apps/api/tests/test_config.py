"""Outside development, missing secrets must stop the app at startup rather
than silently falling back to random per-process values."""

import pytest
from pydantic import ValidationError

from clinically_anchored_api.core.config import Settings

FULL = {
    "checkin_link_secret": "s",
    "audit_signing_key": "k",
    "supabase_url": "https://x.supabase.co",
    "supabase_service_role_key": "r",
}


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_development_generates_a_random_link_secret():
    a, b = _settings(environment="development"), _settings(environment="development")
    assert a.checkin_link_secret and a.checkin_link_secret != b.checkin_link_secret


def test_development_keeps_an_explicit_link_secret():
    s = _settings(environment="development", checkin_link_secret="fixed")
    assert s.checkin_link_secret == "fixed"


def test_production_with_everything_set_is_fine():
    assert _settings(environment="production", **FULL).checkin_link_secret == "s"


@pytest.mark.parametrize("missing", list(FULL))
def test_production_refuses_to_start_without_each_secret(missing):
    with pytest.raises(ValidationError, match=missing.upper()):
        _settings(environment="production", **{**FULL, missing: ""})
