"""Clinician/delegate authentication for the api.

Clinicians sign in through Supabase Auth in apps/web and send their access
token here as `Authorization: Bearer <jwt>`. This service uses the service
role key, which bypasses RLS, so tenant scoping has to be enforced here:
verify the JWT with Supabase, then confirm the user is a member of the clinic
named in the route. Patients never come through this path -- they use the
signed link tokens in core/security.py.
"""

from dataclasses import dataclass

from fastapi import Header, HTTPException

from clinically_anchored_api.core.db import get_supabase


@dataclass(frozen=True)
class ClinicMember:
    user_id: str
    clinic_id: str
    role: str  # owner | clinician | delegate


def require_clinic_member(
    clinic_id: str, authorization: str | None = Header(default=None)
) -> ClinicMember:
    """FastAPI dependency: `clinic_id` is read from the route's path."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    jwt = authorization[7:].strip()

    supabase = get_supabase()
    try:
        user_response = supabase.auth.get_user(jwt)
    except Exception as exc:  # supabase-py raises its own AuthError types
        raise HTTPException(status_code=401, detail="Invalid or expired session.") from exc
    user = getattr(user_response, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")

    result = (
        supabase.table("clinic_members")
        .select("role")
        .eq("clinic_id", clinic_id)
        .eq("user_id", user.id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=403, detail="Not a member of this clinic.")
    return ClinicMember(user_id=user.id, clinic_id=clinic_id, role=result.data[0]["role"])
