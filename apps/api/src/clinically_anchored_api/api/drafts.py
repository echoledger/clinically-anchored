"""AI-drafted replies: generate -> a clinician decides -> (maybe) sent.

A draft is written by the model (core/ai.py) and stored `pending`. Nothing is ever
sent on its own: a message goes out only when a clinician explicitly approves the
draft as written, edits it and sends the edit, or rejects it (nothing sent). The
decision itself is one database function (`decide_message_draft`, migration 6) so
a draft can't be sent twice or marked sent without a message; this module checks
clinic ownership and the clinician's role first, then audits every transition:

  draft.generated / draft.approved / draft.edited / draft.rejected

each carrying the model id and prompt version in its (readable) metadata. Drafts
are retained permanently -- there is no delete route and the database refuses
deletes. Approving or editing also sends a normal clinician message, audited as
`message.sent` like any other."""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from clinically_anchored_api.api.messages import _require_patient_in_clinic, audit_message_sent
from clinically_anchored_api.core import ai
from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.auth import ClinicMember, require_clinic_member
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.schemas import (
    DraftCreate,
    DraftDecisionOut,
    DraftEdit,
    DraftOut,
    MessageOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["drafts"])

_COLUMNS = (
    "id, clinic_id, patient_id, source_message_id, draft_text, model_id, prompt_version, "
    "status, final_text, sent_message_id, created_by, created_at, decided_by, decided_at"
)
_MESSAGE_COLUMNS = "id, clinic_id, patient_id, sender, body, read_at, created_at"

# Approving puts words in the clinic's voice, so it is a clinician act. Delegates
# (e.g. a secretary) can still read drafts and ask for one. Widen deliberately.
DECIDER_ROLES = frozenset({"owner", "clinician"})

_TEMPLATE = ("draft_reply", "v1")
_CONVERSATION_LIMIT = 20
# Clinic protocol notes (data-catalogue D13) aren't modelled yet.
_PROTOCOL_NOTES = "(No clinic protocol notes have been configured yet.)"


def _actor(member: ClinicMember) -> dict:
    return {"type": "member", "id": member.user_id, "role": member.role}


# Extra fields that are safe to show in readable metadata (ids and counts, no content).
_METADATA_KEYS = {"sent_message_id", "input_tokens", "output_tokens"}


def _audit_draft(
    supabase, *, clinic_id: str, event_type: str, draft: dict, member: ClinicMember, **extra
) -> None:
    """One audit event per transition. model_id / prompt_version go in the
    readable metadata; the draft and final text only as part of the hashed
    payload (metadata is ids and labels, never content)."""
    metadata = {
        "ref_type": "draft",
        "ref_id": draft["id"],
        "actor": _actor(member),
        "model_id": draft["model_id"],
        "prompt_version": draft["prompt_version"],
        **{k: v for k, v in extra.items() if k in _METADATA_KEYS},
    }
    payload = {
        "draft_id": draft["id"],
        "patient_id": draft["patient_id"],
        "source_message_id": draft["source_message_id"],
        "draft_text": draft["draft_text"],
        "final_text": draft["final_text"],
        "model_id": draft["model_id"],
        "prompt_version": draft["prompt_version"],
        "status": draft["status"],
        "decided_by": draft["decided_by"],
        "decided_at": draft["decided_at"],
        **extra,
    }
    record_event(
        supabase, clinic_id=clinic_id, event_type=event_type, payload=payload, metadata=metadata
    )


def _get_draft(supabase, clinic_id: str, draft_id: str) -> dict:
    found = (
        supabase.table("message_drafts")
        .select(_COLUMNS)
        .eq("id", draft_id)
        .eq("clinic_id", clinic_id)
        .execute()
        .data
    )
    if not found:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return found[0]


def _pending_for(supabase, clinic_id: str, source_message_id: str) -> dict | None:
    found = (
        supabase.table("message_drafts")
        .select(_COLUMNS)
        .eq("clinic_id", clinic_id)
        .eq("source_message_id", source_message_id)
        .eq("status", "pending")
        .execute()
        .data
    )
    return found[0] if found else None


# --- read -----------------------------------------------------------------------


@router.get("/clinics/{clinic_id}/patients/{patient_id}/drafts", response_model=list[DraftOut])
def list_drafts(
    clinic_id: str,
    patient_id: str,
    status: Literal["pending", "approved", "edited", "rejected"] | None = Query(default=None),
    _member: ClinicMember = Depends(require_clinic_member),
) -> list[DraftOut]:
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)
    query = (
        supabase.table("message_drafts")
        .select(_COLUMNS)
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
    )
    if status:
        query = query.eq("status", status)
    return [DraftOut(**row) for row in query.order("created_at", desc=True).execute().data]


# --- generate -------------------------------------------------------------------


@router.post("/clinics/{clinic_id}/patients/{patient_id}/drafts", response_model=DraftOut)
def generate_draft(
    clinic_id: str,
    patient_id: str,
    body: DraftCreate | None = None,
    member: ClinicMember = Depends(require_clinic_member),
) -> DraftOut:
    """Ask the model for a reply to a patient message (default: the latest one) and
    store it `pending`. Sends nothing. If that message already has a pending draft,
    it is returned as-is and the model is not called again."""
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)

    wanted = body.message_id if body else None
    sources = supabase.table("messages").select(_MESSAGE_COLUMNS)
    sources = sources.eq("clinic_id", clinic_id).eq("patient_id", patient_id)
    sources = sources.eq("sender", "patient")
    if wanted:
        found = sources.eq("id", wanted).execute().data
        if not found:
            raise HTTPException(status_code=404, detail="Patient message not found.")
    else:
        found = sources.order("created_at", desc=True).limit(1).execute().data
        if not found:
            raise HTTPException(status_code=422, detail="This patient has no message to reply to.")
    source = found[0]

    existing = _pending_for(supabase, clinic_id, source["id"])
    if existing:
        return DraftOut(**existing)

    recent = (
        supabase.table("messages")
        .select(_MESSAGE_COLUMNS)
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .order("created_at", desc=True)
        .limit(_CONVERSATION_LIMIT)
        .execute()
        .data
    )
    conversation = "\n".join(f"{m['sender']}: {m['body']}" for m in reversed(recent))
    template = ai.load_template(*_TEMPLATE)
    system, prompt = template.render(
        protocol_notes=_PROTOCOL_NOTES,
        conversation=conversation,
        latest_patient_message=source["body"],
    )

    try:
        result = ai.generate(
            clinic_id=clinic_id,
            prompt_version=template.prompt_version,
            system=system,
            prompt=prompt,
        )
    except ai.AIDailyCapExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ai.AIInvocationError as exc:
        logger.error("draft generation failed for clinic %s: %s", clinic_id, exc)
        raise HTTPException(status_code=502, detail="Draft generation failed. Try again.") from exc
    if not result.text.strip():
        raise HTTPException(status_code=502, detail="The model returned an empty draft.")

    try:
        inserted = (
            supabase.table("message_drafts")
            .insert(
                {
                    "clinic_id": clinic_id,
                    "patient_id": patient_id,
                    "source_message_id": source["id"],
                    "draft_text": result.text.strip(),
                    "model_id": result.model_id,
                    "prompt_version": result.prompt_version,
                    "created_by": member.user_id,
                }
            )
            .execute()
            .data
        )
    except Exception:
        # A concurrent request stored a draft for this message first (unique index).
        winner = _pending_for(supabase, clinic_id, source["id"])
        if winner is None:
            raise
        return DraftOut(**winner)
    if not inserted:
        raise HTTPException(status_code=500, detail="Draft was not saved.")
    row = inserted[0]
    try:
        _audit_draft(
            supabase,
            clinic_id=clinic_id,
            event_type="draft.generated",
            draft=row,
            member=member,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        )
    except AuditWriteError as exc:
        logger.error("draft %s saved but audit write failed: %s", row["id"], exc)
        raise HTTPException(status_code=500, detail="Draft saved but not audited.") from exc
    return DraftOut(**row)


# --- decide ---------------------------------------------------------------------


def _decide(
    clinic_id: str,
    draft_id: str,
    decision: Literal["approved", "edited", "rejected"],
    final_text: str | None,
    member: ClinicMember,
) -> DraftDecisionOut:
    if member.role not in DECIDER_ROLES:
        raise HTTPException(status_code=403, detail="Only a clinician can decide on a draft.")
    supabase = get_supabase()
    draft = _get_draft(supabase, clinic_id, draft_id)  # 404 unless it is this clinic's
    if draft["status"] != "pending":
        raise HTTPException(status_code=409, detail="This draft has already been decided.")
    if decision == "edited" and final_text == draft["draft_text"]:
        raise HTTPException(
            status_code=422, detail="The edit is identical to the draft; approve it instead."
        )

    try:
        outcome = (
            supabase.rpc(
                "decide_message_draft",
                {
                    "p_clinic_id": clinic_id,
                    "p_draft_id": draft_id,
                    "p_decision": decision,
                    "p_final_text": final_text,
                    "p_decided_by": member.user_id,
                },
            )
            .execute()
            .data
        )
    except Exception as exc:
        text = str(exc)
        if "draft_not_pending" in text:  # another clinician decided first
            detail = "This draft has already been decided."
            raise HTTPException(status_code=409, detail=detail) from exc
        if "draft_not_found" in text:
            raise HTTPException(status_code=404, detail="Draft not found.") from exc
        if "draft_edit_invalid" in text:
            raise HTTPException(status_code=422, detail="The edited text is not valid.") from exc
        raise

    decided, sent = outcome["draft"], outcome["message"]
    failed = []
    if sent is not None:
        try:
            audit_message_sent(supabase, clinic_id=clinic_id, row=sent, actor=_actor(member))
        except AuditWriteError as exc:
            failed.append(f"message {sent['id']}: {exc}")
    try:
        _audit_draft(
            supabase,
            clinic_id=clinic_id,
            event_type={
                "approved": "draft.approved",
                "edited": "draft.edited",
                "rejected": "draft.rejected",
            }[decision],
            draft=decided,
            member=member,
            **({"sent_message_id": sent["id"]} if sent else {}),
        )
    except AuditWriteError as exc:
        failed.append(f"draft {decided['id']}: {exc}")
    if failed:
        # The decision (and any send) is already committed; say so rather than
        # pretend the trail is complete. core/reconcile.py lists such rows.
        logger.error("draft %s decided but audit write failed: %s", draft_id, "; ".join(failed))
        raise HTTPException(status_code=500, detail="Draft decided but not fully audited.")

    return DraftDecisionOut(
        draft=DraftOut(**decided), message=MessageOut(**sent) if sent else None
    )


@router.post("/clinics/{clinic_id}/drafts/{draft_id}/approve", response_model=DraftDecisionOut)
def approve_draft(
    clinic_id: str, draft_id: str, member: ClinicMember = Depends(require_clinic_member)
) -> DraftDecisionOut:
    """Send the draft exactly as the model wrote it."""
    return _decide(clinic_id, draft_id, "approved", None, member)


@router.post("/clinics/{clinic_id}/drafts/{draft_id}/edit", response_model=DraftDecisionOut)
def edit_draft(
    clinic_id: str,
    draft_id: str,
    body: DraftEdit,
    member: ClinicMember = Depends(require_clinic_member),
) -> DraftDecisionOut:
    """Send the clinician's edited text; the model's original is kept on the draft."""
    return _decide(clinic_id, draft_id, "edited", body.final_text, member)


@router.post("/clinics/{clinic_id}/drafts/{draft_id}/reject", response_model=DraftDecisionOut)
def reject_draft(
    clinic_id: str, draft_id: str, member: ClinicMember = Depends(require_clinic_member)
) -> DraftDecisionOut:
    """Discard the draft. Nothing is sent."""
    return _decide(clinic_id, draft_id, "rejected", None, member)
