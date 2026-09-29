"""Clinic-scoped endpoints the clinician web app needs around patients:
who am I / which clinics, list and create patients, and issue patient links.

Link issuance stands in for SMS/email delivery during trials: a clinician
copies the returned URL and sends it to the patient themselves."""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.auth import (
    AuthedUser,
    ClinicMember,
    require_clinic_member,
    require_user,
)
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.security import create_link_token, link_max_age
from clinically_anchored_api.schemas import (
    ClinicOut,
    LinkOut,
    MeOut,
    PatientCreate,
    PatientOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["clinics"])

_LINK_PATHS = {"checkin": "/check-in", "messages": "/messages"}


def _audit_or_500(supabase, member: ClinicMember, event_type: str, ref: dict, payload: dict):
    try:
        record_event(
            supabase,
            clinic_id=member.clinic_id,
            event_type=event_type,
            payload=payload,
            metadata={
                **ref,
                "actor": {"type": "member", "id": member.user_id, "role": member.role},
            },
        )
    except AuditWriteError as exc:
        logger.error("%s done but audit write failed: %s", event_type, exc)
        raise HTTPException(status_code=500, detail="Saved but not audited.") from exc


@router.get("/me", response_model=MeOut)
def me(user: AuthedUser = Depends(require_user)) -> MeOut:
    """The signed-in user's clinics, so the web app knows where to route them."""
    supabase = get_supabase()
    memberships = (
        supabase.table("clinic_members")
        .select("clinic_id, role")
        .eq("user_id", user.user_id)
        .execute()
        .data
    )
    clinics: list[ClinicOut] = []
    if memberships:
        roles = {m["clinic_id"]: m["role"] for m in memberships}
        rows = (
            supabase.table("clinics").select("id, name").in_("id", sorted(roles)).execute().data
        )
        clinics = [ClinicOut(id=r["id"], name=r["name"], role=roles[r["id"]]) for r in rows]
    return MeOut(user_id=user.user_id, email=user.email, clinics=clinics)


@router.get("/clinics/{clinic_id}/patients", response_model=list[PatientOut])
def list_patients(
    clinic_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> list[PatientOut]:
    rows = (
        get_supabase()
        .table("patients")
        .select("id, full_name, contact, created_at")
        .eq("clinic_id", clinic_id)
        .order("full_name")
        .execute()
        .data
    )
    return [PatientOut(**r) for r in rows]


@router.post("/clinics/{clinic_id}/patients", response_model=PatientOut)
def create_patient(
    clinic_id: str, body: PatientCreate, member: ClinicMember = Depends(require_clinic_member)
) -> PatientOut:
    supabase = get_supabase()
    result = (
        supabase.table("patients")
        .insert(
            {
                "clinic_id": clinic_id,
                "full_name": body.full_name.strip(),
                "contact": body.contact.strip() if body.contact else None,
            }
        )
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Patient was not saved.")
    row = result.data[0]
    # Identity fields are hashed into the audit payload, never stored in the log.
    _audit_or_500(
        supabase,
        member,
        "patient.created",
        {"ref_type": "patient", "ref_id": row["id"]},
        {"patient_id": row["id"], "full_name": row["full_name"], "contact": row["contact"]},
    )
    return PatientOut(**row)


@router.post("/clinics/{clinic_id}/patients/{patient_id}/links/{scope}", response_model=LinkOut)
def issue_link(
    clinic_id: str,
    patient_id: str,
    scope: str,
    member: ClinicMember = Depends(require_clinic_member),
) -> LinkOut:
    """Mint a signed link for one patient (`checkin` or `messages`). Anyone
    holding the URL can use it until it expires, so hand it only to the
    patient. Every issuance is audited (without the token itself)."""
    if scope not in _LINK_PATHS:
        raise HTTPException(status_code=404, detail="Unknown link type.")

    settings = get_settings()
    base = settings.web_base_url or (
        "http://localhost:3000" if settings.environment == "development" else ""
    )
    if not base:
        raise HTTPException(status_code=503, detail="WEB_BASE_URL is not configured.")

    supabase = get_supabase()
    found = (
        supabase.table("patients")
        .select("id")
        .eq("id", patient_id)
        .eq("clinic_id", clinic_id)
        .execute()
        .data
    )
    if not found:
        raise HTTPException(status_code=404, detail="Patient not found.")

    token = create_link_token(clinic_id=clinic_id, patient_id=patient_id, scope=scope)
    expires_at = (datetime.now(UTC) + timedelta(seconds=link_max_age(scope))).isoformat()
    _audit_or_500(
        supabase,
        member,
        "link.issued",
        {"ref_type": "patient", "ref_id": patient_id, "scope": scope},
        {"patient_id": patient_id, "scope": scope, "expires_at": expires_at},
    )
    url = f"{base.rstrip('/')}{_LINK_PATHS[scope]}?token={token}"
    return LinkOut(scope=scope, url=url, expires_at=expires_at)
