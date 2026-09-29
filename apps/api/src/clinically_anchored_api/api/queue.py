"""Clinician-side view of check-ins: the triage queue, a filterable check-in
list, and marking a check-in reviewed.

All routes are scoped to a clinic through `require_clinic_member`, and every
query filters on clinic_id explicitly -- the service role bypasses RLS."""

import logging
from collections import defaultdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.auth import ClinicMember, require_clinic_member
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.schemas import CheckInDetail, QueueItem

logger = logging.getLogger(__name__)

router = APIRouter(tags=["queue"])

_CHECK_IN_COLS = (
    "id, patient_id, procedure_id, post_op_day, answers, is_red_flag, "
    "created_at, reviewed_at, reviewed_by"
)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _patient_names(supabase, clinic_id: str, patient_ids: set[str]) -> dict[str, str]:
    if not patient_ids:
        return {}
    rows = (
        supabase.table("patients")
        .select("id, full_name")
        .eq("clinic_id", clinic_id)
        .in_("id", sorted(patient_ids))
        .execute()
        .data
    )
    return {r["id"]: r["full_name"] for r in rows}


def _procedure_names(supabase, clinic_id: str) -> dict[str, str]:
    rows = (
        supabase.table("procedures").select("id, name").eq("clinic_id", clinic_id).execute().data
    )
    return {r["id"]: r["name"] for r in rows}


def _detail(row: dict, patients: dict[str, str], procedures: dict[str, str]) -> CheckInDetail:
    return CheckInDetail(
        **row,
        patient_name=patients.get(row["patient_id"]),
        procedure_name=procedures.get(row["procedure_id"]) if row["procedure_id"] else None,
    )


@router.get("/clinics/{clinic_id}/queue", response_model=list[QueueItem])
def get_queue(
    clinic_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> list[QueueItem]:
    """Patients needing attention, most urgent first: anyone with an
    unreviewed red-flag check-in, then everyone else by latest activity.
    A patient drops off once their check-ins are reviewed and their messages
    read. (Reads at most the platform's row cap, 1000 each -- fine for v0.)"""
    supabase = get_supabase()
    check_ins = (
        supabase.table("check_ins")
        .select("id, patient_id, post_op_day, is_red_flag, created_at")
        .eq("clinic_id", clinic_id)
        .is_("reviewed_at", "null")
        .order("created_at", desc=True)
        .execute()
        .data
    )
    unread = (
        supabase.table("messages")
        .select("id, patient_id, created_at")
        .eq("clinic_id", clinic_id)
        .eq("sender", "patient")
        .is_("read_at", "null")
        .execute()
        .data
    )

    check_ins_by_patient: dict[str, list[dict]] = defaultdict(list)
    for row in check_ins:
        check_ins_by_patient[row["patient_id"]].append(row)
    unread_by_patient: dict[str, list[dict]] = defaultdict(list)
    for row in unread:
        unread_by_patient[row["patient_id"]].append(row)

    patient_ids = set(check_ins_by_patient) | set(unread_by_patient)
    names = _patient_names(supabase, clinic_id, patient_ids)

    items: list[QueueItem] = []
    for pid in patient_ids:
        cis = check_ins_by_patient.get(pid, [])
        msgs = unread_by_patient.get(pid, [])
        latest = cis[0] if cis else None  # check_ins are newest-first
        activity = [r["created_at"] for r in cis] + [r["created_at"] for r in msgs]
        items.append(
            QueueItem(
                patient_id=pid,
                patient_name=names.get(pid),
                has_red_flag=any(r["is_red_flag"] for r in cis),
                unreviewed_check_ins=len(cis),
                unread_messages=len(msgs),
                latest_check_in_id=latest["id"] if latest else None,
                latest_post_op_day=latest["post_op_day"] if latest else None,
                last_activity_at=max(activity, key=_ts),
            )
        )

    items.sort(key=lambda i: _ts(i.last_activity_at), reverse=True)
    items.sort(key=lambda i: not i.has_red_flag)  # stable: red flags to the front
    return items


@router.get("/clinics/{clinic_id}/check-ins", response_model=list[CheckInDetail])
def list_check_ins(
    clinic_id: str,
    unreviewed_only: bool = False,
    red_flag_only: bool = False,
    patient_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    _member: ClinicMember = Depends(require_clinic_member),
) -> list[CheckInDetail]:
    supabase = get_supabase()
    query = supabase.table("check_ins").select(_CHECK_IN_COLS).eq("clinic_id", clinic_id)
    if unreviewed_only:
        query = query.is_("reviewed_at", "null")
    if red_flag_only:
        query = query.eq("is_red_flag", True)
    if patient_id:
        query = query.eq("patient_id", patient_id)
    rows = query.order("created_at", desc=True).limit(limit).execute().data

    names = _patient_names(supabase, clinic_id, {r["patient_id"] for r in rows})
    procedures = _procedure_names(supabase, clinic_id)
    return [_detail(r, names, procedures) for r in rows]


@router.post("/clinics/{clinic_id}/check-ins/{check_in_id}/review", response_model=CheckInDetail)
def review_check_in(
    clinic_id: str,
    check_in_id: str,
    member: ClinicMember = Depends(require_clinic_member),
) -> CheckInDetail:
    """Mark a check-in reviewed by the calling clinician/delegate. Idempotent:
    the first review wins and is the only one audited."""
    supabase = get_supabase()

    def fetch() -> dict:
        found = (
            supabase.table("check_ins")
            .select(_CHECK_IN_COLS)
            .eq("id", check_in_id)
            .eq("clinic_id", clinic_id)
            .execute()
            .data
        )
        if not found:
            raise HTTPException(status_code=404, detail="Check-in not found.")
        return found[0]

    row = fetch()
    if row["reviewed_at"] is None:
        # `is null` in the update makes concurrent reviewers race safely: only
        # one update matches, and only that caller writes the audit event.
        updated = (
            supabase.table("check_ins")
            .update({"reviewed_at": datetime.now(UTC).isoformat(), "reviewed_by": member.user_id})
            .eq("id", check_in_id)
            .eq("clinic_id", clinic_id)
            .is_("reviewed_at", "null")
            .execute()
            .data
        )
        if updated:
            row = updated[0]
            try:
                record_event(
                    supabase,
                    clinic_id=clinic_id,
                    event_type="check_in.reviewed",
                    payload={
                        "check_in_id": check_in_id,
                        "reviewed_by": member.user_id,
                        "reviewed_at": row["reviewed_at"],
                    },
                    metadata={
                        "ref_type": "check_in",
                        "ref_id": check_in_id,
                        "actor": {"type": "member", "id": member.user_id, "role": member.role},
                    },
                )
            except AuditWriteError as exc:
                logger.error("check-in %s reviewed but audit write failed: %s", check_in_id, exc)
                raise HTTPException(
                    status_code=500, detail="Check-in reviewed but not audited."
                ) from exc
        else:
            row = fetch()  # lost the race; report the winner's review

    names = _patient_names(supabase, clinic_id, {row["patient_id"]})
    return _detail(row, names, _procedure_names(supabase, clinic_id))
