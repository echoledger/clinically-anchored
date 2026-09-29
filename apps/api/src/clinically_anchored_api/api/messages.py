"""Clinician-side message endpoints (patient side waits on the link-token
flow for messaging -- see docs/api/README.md backlog item 3).

Every route is scoped by clinic through `require_clinic_member`, and every
patient/message id is re-checked against that clinic: the service role
bypasses RLS, so a clinic_id in the URL is only trustworthy once we've
confirmed the rows we touch actually belong to it."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from clinically_anchored_api.core.auth import ClinicMember, require_clinic_member
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.schemas import MessageCreate, MessageOut

router = APIRouter(tags=["messages"])

_COLUMNS = "id, clinic_id, patient_id, sender, body, read_at, created_at"


def _require_patient_in_clinic(supabase, clinic_id: str, patient_id: str) -> None:
    result = (
        supabase.table("patients")
        .select("id")
        .eq("id", patient_id)
        .eq("clinic_id", clinic_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Patient not found.")


@router.get(
    "/clinics/{clinic_id}/patients/{patient_id}/messages", response_model=list[MessageOut]
)
def list_thread(
    clinic_id: str, patient_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> list[MessageOut]:
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)
    result = (
        supabase.table("messages")
        .select(_COLUMNS)
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .order("created_at")
        .execute()
    )
    return [MessageOut(**row) for row in result.data]


@router.post(
    "/clinics/{clinic_id}/patients/{patient_id}/messages", response_model=MessageOut
)
def send_message(
    clinic_id: str,
    patient_id: str,
    body: MessageCreate,
    _member: ClinicMember = Depends(require_clinic_member),
) -> MessageOut:
    """Clinician/delegate -> patient. Always stored with sender='clinician';
    the caller can't choose the sender. Nothing here is AI-drafted, so
    there's no approval step to record yet."""
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)
    result = (
        supabase.table("messages")
        .insert(
            {
                "clinic_id": clinic_id,
                "patient_id": patient_id,
                "sender": "clinician",
                "body": body.body,
            }
        )
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Message was not saved.")
    return MessageOut(**result.data[0])


@router.post("/clinics/{clinic_id}/messages/{message_id}/read", response_model=MessageOut)
def mark_read(
    clinic_id: str, message_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> MessageOut:
    """Mark a patient's message as read by the clinic. Idempotent: only sets
    read_at the first time."""
    supabase = get_supabase()
    existing = (
        supabase.table("messages")
        .select(_COLUMNS)
        .eq("id", message_id)
        .eq("clinic_id", clinic_id)
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=404, detail="Message not found.")
    row = existing.data[0]
    if row["sender"] != "patient" or row["read_at"] is not None:
        return MessageOut(**row)

    updated = (
        supabase.table("messages")
        .update({"read_at": datetime.now(UTC).isoformat()})
        .eq("id", message_id)
        .eq("clinic_id", clinic_id)
        .execute()
    )
    return MessageOut(**updated.data[0])
