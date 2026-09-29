import logging

from fastapi import APIRouter, Depends, HTTPException

from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.rules import evaluate_red_flags
from clinically_anchored_api.core.security import require_checkin_token
from clinically_anchored_api.schemas import CheckInContext, CheckInCreate, CheckInOut, Procedure

logger = logging.getLogger(__name__)

router = APIRouter(tags=["check-ins"])


@router.get("/check-ins/context", response_model=CheckInContext)
def check_in_context(claims: dict[str, str] = Depends(require_checkin_token)) -> CheckInContext:
    """What the check-in page needs to render: the clinic (resolved from the
    token, never exposed in the URL) and its active procedures."""
    clinic_id = claims["clinic_id"]

    supabase = get_supabase()
    result = (
        supabase.table("procedures")
        .select("id, name")
        .eq("clinic_id", clinic_id)
        .eq("active", True)
        .order("name")
        .execute()
    )
    procedures = [Procedure(**row) for row in result.data]
    return CheckInContext(clinic_id=clinic_id, procedures=procedures)


@router.post("/check-ins", response_model=CheckInOut)
def submit_check_in(
    body: CheckInCreate, claims: dict[str, str] = Depends(require_checkin_token)
) -> CheckInOut:
    clinic_id = claims["clinic_id"]
    patient_id = claims["patient_id"]

    is_red_flag, _matched_rules = evaluate_red_flags(body.answers)

    supabase = get_supabase()
    if body.procedure_id is not None:
        # The service role bypasses RLS and check_ins.procedure_id only has a
        # plain FK, so nothing else stops a token for one clinic from naming
        # another clinic's procedure.
        known = (
            supabase.table("procedures")
            .select("id")
            .eq("id", body.procedure_id)
            .eq("clinic_id", clinic_id)
            .execute()
        )
        if not known.data:
            raise HTTPException(status_code=422, detail="Unknown procedure for this clinic.")

    result = (
        supabase.table("check_ins")
        .insert(
            {
                "clinic_id": clinic_id,
                "patient_id": patient_id,
                "procedure_id": body.procedure_id,
                "post_op_day": body.post_op_day,
                "answers": body.answers,
                "is_red_flag": is_red_flag,
            }
        )
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=500, detail="Check-in was not saved.")

    row = result.data[0]
    try:
        record_event(
            supabase,
            clinic_id=clinic_id,
            event_type="check_in.submitted",
            payload={
                "check_in_id": row["id"],
                "patient_id": patient_id,
                "procedure_id": row["procedure_id"],
                "post_op_day": row["post_op_day"],
                "answers": row["answers"],
                "is_red_flag": row["is_red_flag"],
            },
            metadata={
                "ref_type": "check_in",
                "ref_id": row["id"],
                "actor": {"type": "patient", "id": patient_id},
            },
        )
    except AuditWriteError as exc:
        # The check-in row is already written; surface the gap loudly rather
        # than pretend the trail is complete.
        logger.error("check-in %s saved but audit write failed: %s", row["id"], exc)
        raise HTTPException(status_code=500, detail="Check-in saved but not audited.") from exc

    return CheckInOut(**row)
