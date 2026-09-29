from fastapi import APIRouter

from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.schemas import Procedure

router = APIRouter(tags=["procedures"])


@router.get("/clinics/{clinic_id}/procedures", response_model=list[Procedure])
def list_procedures(clinic_id: str) -> list[Procedure]:
    """Active procedures for a clinic's check-in dropdown. Public-ish (no
    patient token required) since it's just clinic-authored reference data,
    not clinical or identifying -- same as the dropdown a receptionist could
    read off a wall poster."""
    supabase = get_supabase()
    result = (
        supabase.table("procedures")
        .select("id, name")
        .eq("clinic_id", clinic_id)
        .eq("active", True)
        .order("name")
        .execute()
    )
    return [Procedure(**row) for row in result.data]
