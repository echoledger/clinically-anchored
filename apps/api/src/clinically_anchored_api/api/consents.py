"""Consent records (data-catalogue D12): storage and audit only.

What consents exist, what their wording says, and whether a missing consent
blocks anything (check-in, messaging) are product/legal decisions that haven't
been made. So this module is deliberately generic and enforces nothing: it
records that a patient granted or withdrew a `consent_type` as worded in a
given `consent_text_version`, audits every change, and lets clinicians read the
state. Gating, when it's decided, belongs in the routes it applies to.

Patients grant and withdraw through their link token (either scope -- the web
app decides which flow asks); clinicians read per patient. Every grant and
withdrawal is audited as `consent.granted` / `consent.revoked`."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path

from clinically_anchored_api.api.messages import _require_patient_in_clinic
from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.auth import ClinicMember, require_clinic_member
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.security import require_any_link_token
from clinically_anchored_api.schemas import (
    CONSENT_TYPE_PATTERN,
    ConsentGrant,
    ConsentOut,
    ConsentSummaryOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["consents"])

_COLUMNS = "id, patient_id, consent_type, consent_text_version, granted_at, revoked_at"


def _summary(supabase, clinic_id: str, patient_id: str) -> ConsentSummaryOut:
    rows = (
        supabase.table("consents")
        .select(_COLUMNS)
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .order("granted_at", desc=True)
        .execute()
        .data
    )
    active = sorted({r["consent_type"] for r in rows if r["revoked_at"] is None})
    return ConsentSummaryOut(
        patient_id=patient_id, active=active, records=[ConsentOut(**r) for r in rows]
    )


def _audit(supabase, *, clinic_id: str, event_type: str, row: dict, patient_id: str, payload: dict):
    """Audit one consent row change; the patient is always the actor here."""
    try:
        record_event(
            supabase,
            clinic_id=clinic_id,
            event_type=event_type,
            payload={"consent_id": row["id"], "patient_id": patient_id, **payload},
            metadata={
                "ref_type": "consent",
                "ref_id": row["id"],
                "actor": {"type": "patient", "id": patient_id},
            },
        )
    except AuditWriteError as exc:
        logger.error("%s on consent %s done but audit write failed: %s", event_type, row["id"], exc)
        raise HTTPException(status_code=500, detail="Consent saved but not audited.") from exc


@router.get(
    "/clinics/{clinic_id}/patients/{patient_id}/consents", response_model=ConsentSummaryOut
)
def clinician_read_consents(
    clinic_id: str, patient_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> ConsentSummaryOut:
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)
    return _summary(supabase, clinic_id, patient_id)


# --- Patient side: authenticated by the signed link token, no account. -------


@router.get("/consents", response_model=ConsentSummaryOut)
def patient_read_consents(
    claims: dict[str, str] = Depends(require_any_link_token),
) -> ConsentSummaryOut:
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, claims["clinic_id"], claims["patient_id"])
    return _summary(supabase, claims["clinic_id"], claims["patient_id"])


@router.post("/consents", response_model=ConsentSummaryOut)
def patient_grant_consent(
    body: ConsentGrant, claims: dict[str, str] = Depends(require_any_link_token)
) -> ConsentSummaryOut:
    """Record that the patient granted `consent_type` as worded in
    `consent_text_version`. Idempotent per (type, version): repeating a live
    grant changes and audits nothing. A new version is a new grant."""
    clinic_id, patient_id = claims["clinic_id"], claims["patient_id"]
    allowed = get_settings().consent_types_list
    if allowed and body.consent_type not in allowed:
        raise HTTPException(status_code=422, detail="Unknown consent type.")

    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)

    def live_grant() -> list[dict]:
        return (
            supabase.table("consents")
            .select(_COLUMNS)
            .eq("clinic_id", clinic_id)
            .eq("patient_id", patient_id)
            .eq("consent_type", body.consent_type)
            .eq("consent_text_version", body.consent_text_version)
            .is_("revoked_at", "null")
            .execute()
            .data
        )

    if not live_grant():
        try:
            inserted = (
                supabase.table("consents")
                .insert(
                    {
                        "clinic_id": clinic_id,
                        "patient_id": patient_id,
                        "consent_type": body.consent_type,
                        "consent_text_version": body.consent_text_version,
                    }
                )
                .execute()
                .data
            )
        except Exception:
            # A concurrent identical grant won the unique index: same outcome.
            if not live_grant():
                raise
            inserted = None
        if inserted:
            row = inserted[0]
            _audit(
                supabase,
                clinic_id=clinic_id,
                event_type="consent.granted",
                row=row,
                patient_id=patient_id,
                payload={
                    "consent_type": row["consent_type"],
                    "consent_text_version": row["consent_text_version"],
                    "granted_at": row["granted_at"],
                },
            )
    return _summary(supabase, clinic_id, patient_id)


@router.post("/consents/{consent_type}/revoke", response_model=ConsentSummaryOut)
def patient_revoke_consent(
    consent_type: str = Path(pattern=CONSENT_TYPE_PATTERN),
    claims: dict[str, str] = Depends(require_any_link_token),
) -> ConsentSummaryOut:
    """Withdraw `consent_type`: ends every live grant of it (any wording
    version). Idempotent; each grant actually ended is audited."""
    clinic_id, patient_id = claims["clinic_id"], claims["patient_id"]
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)

    live = (
        supabase.table("consents")
        .select(_COLUMNS)
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .eq("consent_type", consent_type)
        .is_("revoked_at", "null")
        .execute()
        .data
    )
    for grant in live:
        # `is null` makes concurrent withdrawals race safely: only the update
        # that matches writes the audit event.
        ended = (
            supabase.table("consents")
            .update({"revoked_at": datetime.now(UTC).isoformat()})
            .eq("id", grant["id"])
            .eq("clinic_id", clinic_id)
            .is_("revoked_at", "null")
            .execute()
            .data
        )
        if ended:
            row = ended[0]
            _audit(
                supabase,
                clinic_id=clinic_id,
                event_type="consent.revoked",
                row=row,
                patient_id=patient_id,
                payload={
                    "consent_type": row["consent_type"],
                    "consent_text_version": row["consent_text_version"],
                    "granted_at": row["granted_at"],
                    "revoked_at": row["revoked_at"],
                },
            )
    return _summary(supabase, clinic_id, patient_id)
