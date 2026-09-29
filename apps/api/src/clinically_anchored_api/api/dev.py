"""Dev-only endpoints. Never mounted unless ENVIRONMENT=development -- see
main.py, which only includes this router in that case. This exists purely
to stand in for the real check-in link delivery flow (SMS/email), which
doesn't exist yet."""

from fastapi import APIRouter

from clinically_anchored_api.core.security import create_checkin_token
from clinically_anchored_api.schemas import DevCheckinLinkRequest, DevCheckinLinkResponse

router = APIRouter(prefix="/dev", tags=["dev"])


@router.post("/check-in-links", response_model=DevCheckinLinkResponse)
def mint_check_in_link(body: DevCheckinLinkRequest) -> DevCheckinLinkResponse:
    token = create_checkin_token(clinic_id=body.clinic_id, patient_id=body.patient_id)
    return DevCheckinLinkResponse(token=token)
