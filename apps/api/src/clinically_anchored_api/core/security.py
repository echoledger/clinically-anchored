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

from typing import Literal

from fastapi import HTTPException, Query
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from clinically_anchored_api.core.config import get_settings

Scope = Literal["checkin", "messages"]


class InvalidCheckinToken(Exception):
    """Raised when a link token is missing, tampered with, expired, or issued
    for a different purpose than the route it's used on."""


def _serializer(scope: Scope) -> URLSafeTimedSerializer:
    settings = get_settings()
    # Scope goes into the signing salt as well as the payload, so a token
    # minted for one purpose fails signature verification for the other.
    return URLSafeTimedSerializer(settings.checkin_link_secret, salt=f"link:{scope}")


def link_max_age(scope: Scope) -> int:
    settings = get_settings()
    if scope == "messages":
        return settings.message_link_max_age_seconds
    return settings.checkin_link_max_age_seconds


def create_link_token(*, clinic_id: str, patient_id: str, scope: Scope) -> str:
    return _serializer(scope).dumps({"clinic_id": clinic_id, "patient_id": patient_id})


def create_checkin_token(*, clinic_id: str, patient_id: str) -> str:
    return create_link_token(clinic_id=clinic_id, patient_id=patient_id, scope="checkin")


def verify_link_token(token: str, scope: Scope) -> dict[str, str]:
    """Returns {"clinic_id": ..., "patient_id": ...} or raises InvalidCheckinToken."""
    try:
        data = _serializer(scope).loads(token, max_age=link_max_age(scope))
    except SignatureExpired as exc:
        raise InvalidCheckinToken("This link has expired.") from exc
    except BadSignature as exc:
        raise InvalidCheckinToken("This link is not valid.") from exc

    if "clinic_id" not in data or "patient_id" not in data:
        raise InvalidCheckinToken("This link is not valid.")
    return data


def _require(scope: Scope):
    def dependency(
        token: str = Query(..., description=f"Signed patient link token (scope: {scope})"),
    ) -> dict[str, str]:
        try:
            return verify_link_token(token, scope)
        except InvalidCheckinToken as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    return dependency


# FastAPI dependencies for patient-facing routes: 401 unless the token is valid,
# unexpired and issued for that purpose. Clinic and patient come from the
# token, never from the URL or body.
require_checkin_token = _require("checkin")
require_messages_token = _require("messages")
