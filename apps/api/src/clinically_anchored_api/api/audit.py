from fastapi import APIRouter, Depends

from clinically_anchored_api.core.audit import (
    fetch_chain,
    public_key_b64,
    trusted_public_keys,
    verify_chain,
)
from clinically_anchored_api.core.auth import ClinicMember, require_clinic_member
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.schemas import AuditVerifyOut

router = APIRouter(tags=["audit"])


@router.get("/clinics/{clinic_id}/audit-log/verify", response_model=AuditVerifyOut)
def verify_audit_log(
    clinic_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> AuditVerifyOut:
    """Re-check the clinic's whole chain (links, hashes, signatures), using the
    public key named by each row's key_id. The current public key is returned
    so a third party can repeat the check for rows signed with it."""
    rows = fetch_chain(get_supabase(), clinic_id)
    result = verify_chain(rows, trusted_public_keys())
    return AuditVerifyOut(**result, public_key=public_key_b64())
