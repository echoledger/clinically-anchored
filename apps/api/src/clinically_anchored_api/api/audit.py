import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends

from clinically_anchored_api.core.audit import fetch_chain, public_key_b64, verify_chain
from clinically_anchored_api.core.auth import ClinicMember, require_clinic_member
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.schemas import AuditVerifyOut

router = APIRouter(tags=["audit"])


@router.get("/clinics/{clinic_id}/audit-log/verify", response_model=AuditVerifyOut)
def verify_audit_log(
    clinic_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> AuditVerifyOut:
    """Re-check the clinic's whole chain (links, hashes, signatures). The
    public key is returned so a third party can repeat the check themselves."""
    rows = fetch_chain(get_supabase(), clinic_id)
    pub = public_key_b64()
    result = verify_chain(rows, Ed25519PublicKey.from_public_bytes(base64.b64decode(pub)))
    return AuditVerifyOut(**result, public_key=pub)
