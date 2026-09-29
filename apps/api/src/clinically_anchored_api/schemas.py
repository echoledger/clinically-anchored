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
