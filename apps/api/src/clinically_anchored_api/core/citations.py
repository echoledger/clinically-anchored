"""Parse an AI-written summary into cited lines and verify the citations.

A summary is only worth showing a clinician if every claim can be traced to a
real message in that patient's thread. So the rules here are strict and all-or-
nothing: the model must write one claim per line, each ending in the id(s) of the
message(s) it came from in square brackets. Anything else -- a line with no
citation, empty brackets, a citation that isn't a message id, a bracket in the
middle of the text, or an id that isn't a message in *this* patient's thread --
raises CitationError. Nothing is repaired and no citation is silently dropped;
the caller discards the whole summary.

The existence check asks the database (clinic + patient scoped), not just "was it
in the prompt", because the service role bypasses RLS and a model can invent ids.
"""

import re
import uuid
from dataclasses import dataclass

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
# One or more bracket groups at the very end of the line: "[a]", "[a, b]", "[a] [b]".
_TRAILING = re.compile(r"((?:\s*\[[^\[\]]*\])+)\s*$")
_GROUP = re.compile(r"\[([^\[\]]*)\]")


class CitationError(Exception):
    """The summary can't be shown: it's malformed or its citations don't verify.
    `reason` is safe to log (it never contains summary text); `message_ids` lists
    the offending citations when there are any."""

    def __init__(self, reason: str, message_ids: tuple[str, ...] = ()):
        self.reason, self.message_ids = reason, message_ids
        super().__init__(reason + (f": {', '.join(message_ids)}" if message_ids else ""))


@dataclass(frozen=True)
class SummaryLine:
    text: str
    citations: tuple[str, ...]  # canonical lowercase message ids, in order, no duplicates


def _canonical_id(raw: str) -> str:
    try:
        return str(uuid.UUID(raw.strip()))
    except ValueError:
        raise CitationError("citation is not a message id", (raw.strip()[:64],)) from None


def parse_summary(raw: str) -> list[SummaryLine]:
    """Split the model's output into cited lines. Raises CitationError."""
    lines: list[SummaryLine] = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        body = _BULLET.sub("", line).strip()
        trailing = _TRAILING.search(body)
        if not trailing:
            raise CitationError(f"line {number} has no citation")
        text = body[: trailing.start()].strip()
        if not text:
            raise CitationError(f"line {number} is only a citation")
        if "[" in text or "]" in text:
            raise CitationError(f"line {number} has a bracket that is not a trailing citation")
        ids: list[str] = []
        for group in _GROUP.findall(trailing.group(1)):
            parts = [p for p in re.split(r"[,\s]+", group) if p]
            if not parts:
                raise CitationError(f"line {number} has an empty citation")
            ids.extend(_canonical_id(p) for p in parts)
        lines.append(SummaryLine(text=text, citations=tuple(dict.fromkeys(ids))))
    if not lines:
        raise CitationError("summary is empty")
    return lines


def check_citations(lines: list[SummaryLine], thread_ids: set[str]) -> None:
    """Every cited id must be in `thread_ids` (the ids that exist in the patient's
    thread). Reports ALL offending ids, never just the first."""
    unknown = [i for i in dict.fromkeys(c for ln in lines for c in ln.citations)
               if i not in thread_ids]
    if unknown:
        raise CitationError(
            "cites messages that are not in this patient's thread", tuple(unknown)
        )


def verify_summary(supabase, *, clinic_id: str, patient_id: str, raw: str) -> list[SummaryLine]:
    """Parse `raw` and confirm, against the database, that every cited message
    exists and belongs to this clinic's thread with this patient. Returns the
    verified lines or raises CitationError."""
    lines = parse_summary(raw)
    cited = sorted({c for ln in lines for c in ln.citations})
    found = (
        supabase.table("messages")
        .select("id")
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .in_("id", cited)
        .execute()
        .data
    )
    check_citations(lines, {str(row["id"]).lower() for row in found})
    return lines
