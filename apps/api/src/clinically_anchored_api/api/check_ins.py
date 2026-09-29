from fastapi import APIRouter, HTTPException, Query

from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.rules import evaluate_red_flags
from clinically_anchored_api.core.security import InvalidCheckinToken, verify_checkin_token
from clinically_anchored_api.schemas import CheckInContext, CheckInCreate, CheckInOut, Procedure

router = APIRouter(tags=["check-ins"])


def _resolve_token(token: str) -> dict[str, str]:
    try:
        return verify_checkin_token(token)
    except InvalidCheckinToken as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/check-ins/context", response_model=CheckInContext)
def check_in_context(
    token: str = Query(..., description="Signed check-in link token"),
) -> CheckInContext:
    """What the check-in page needs to render: the clinic (resolved from the
    token, never exposed in the URL) and its active procedures."""
    claims = _resolve_token(token)
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
    body: CheckInCreate,
    token: str = Query(..., description="Signed check-in link token"),
) -> CheckInOut:
    claims = _resolve_token(token)
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

    return CheckInOut(**result.data[0])
