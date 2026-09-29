"""Signed, expiring tokens for patient check-in links.

Patients are not Supabase Auth users (see docs/database) -- there is no
patient password. Instead, a patient reaches their check-in form through a
link containing one of these tokens. The token itself carries the clinic and
patient it's valid for; verifying it needs no database round trip, only the
shared secret, which only this service holds.

This is a deliberately simple v0 mechanism: a signed, timestamped, URL-safe
token (itsdangerous), no separate token table. It is NOT yet the real
delivery flow -- nothing sends these over SMS/email today. See the dev-only
mint endpoint in api/dev.py for how a token gets created for now.
"""

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from clinically_anchored_api.core.config import get_settings


class InvalidCheckinToken(Exception):
    """Raised when a check-in link token is missing, tampered with, or expired."""


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.checkin_link_secret, salt="checkin-link")


def create_checkin_token(*, clinic_id: str, patient_id: str) -> str:
    return _serializer().dumps({"clinic_id": clinic_id, "patient_id": patient_id})


def verify_checkin_token(token: str) -> dict[str, str]:
    """Returns {"clinic_id": ..., "patient_id": ...} or raises InvalidCheckinToken."""
    settings = get_settings()
    try:
        data = _serializer().loads(token, max_age=settings.checkin_link_max_age_seconds)
    except SignatureExpired as exc:
        raise InvalidCheckinToken("This link has expired.") from exc
    except BadSignature as exc:
        raise InvalidCheckinToken("This link is not valid.") from exc

    if "clinic_id" not in data or "patient_id" not in data:
        raise InvalidCheckinToken("This link is not valid.")
    return data
