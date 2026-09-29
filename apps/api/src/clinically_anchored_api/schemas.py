"""Pydantic request/response models. Kept separate from the FastAPI routers
so the shapes are easy to scan in one place -- this file is effectively the
contract apps/web's generated OpenAPI client is built from."""

from typing import Literal

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
    scope: Literal["checkin", "messages"] = "checkin"


class DevCheckinLinkResponse(BaseModel):
    token: str
    scope: str


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
    key_ids: list[str]  # signing keys that appear in this clinic's chain
    public_key: str  # base64 Ed25519 public key of the currently active signing key


class CheckInDetail(BaseModel):
    """A check-in as a clinician sees it: answers plus resolved names."""

    id: str
    patient_id: str
    patient_name: str | None
    procedure_id: str | None
    procedure_name: str | None
    post_op_day: int | None
    answers: dict
    is_red_flag: bool
    created_at: str
    reviewed_at: str | None
    reviewed_by: str | None


class QueueItem(BaseModel):
    """One patient who needs attention: unreviewed check-ins and/or unread
    patient messages. Ordered by the endpoint, unreviewed red flags first."""

    patient_id: str
    patient_name: str | None
    has_red_flag: bool
    unreviewed_check_ins: int
    unread_messages: int
    latest_check_in_id: str | None
    latest_post_op_day: int | None
    last_activity_at: str


class ClinicOut(BaseModel):
    id: str
    name: str
    role: str


class MeOut(BaseModel):
    user_id: str
    email: str | None
    clinics: list[ClinicOut]


class PatientCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    # Phone or email the check-in link is sent to (free text for now).
    contact: str | None = Field(default=None, max_length=200)


class PatientOut(BaseModel):
    id: str
    full_name: str
    contact: str | None
    created_at: str


class LinkOut(BaseModel):
    scope: Literal["checkin", "messages"]
    url: str
    expires_at: str
