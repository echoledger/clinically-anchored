"""Pydantic request/response models. Kept separate from the FastAPI routers
so the shapes are easy to scan in one place -- this file is effectively the
contract apps/web's generated OpenAPI client is built from."""

from pydantic import BaseModel, Field


class Procedure(BaseModel):
    id: str
    name: str


class CheckInCreate(BaseModel):
    procedure_id: str | None = None
    post_op_day: int | None = Field(default=None, ge=0)
    # Checkbox/dropdown answers from the structured check-in form. Shape is
    # intentionally loose for now (this is what the rule engine reads) --
    # tighten once the actual question set from Sarah is settled.
    answers: dict = Field(default_factory=dict)


class CheckInOut(BaseModel):
    id: str
    clinic_id: str
    patient_id: str
    procedure_id: str | None
    post_op_day: int | None
    answers: dict
    is_red_flag: bool
    created_at: str


class DevCheckinLinkRequest(BaseModel):
    clinic_id: str
    patient_id: str


class DevCheckinLinkResponse(BaseModel):
    token: str


class CheckInContext(BaseModel):
    """What the check-in page needs to render its form, resolved from the
    token alone -- the patient's link never carries the clinic id directly."""

    clinic_id: str
    procedures: list[Procedure]


class MessageCreate(BaseModel):
    # Messages are free-form text from both sides (decision 5); the cap is a
    # sanity limit, not a product rule.
    body: str = Field(min_length=1, max_length=5000)


class MessageOut(BaseModel):
    id: str
    clinic_id: str
    patient_id: str
    sender: str
    body: str
    read_at: str | None
    created_at: str


class AuditVerifyOut(BaseModel):
    ok: bool
    count: int
    head_hash: str | None
    broken_at: int | None
    reason: str | None
    public_key: str  # base64 Ed25519 public key the chain was checked against
