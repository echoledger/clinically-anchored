"""Citation parsing and verification (core/citations.py), tested directly: valid
citations pass; malformed ones, and ones to nonexistent or wrong-thread messages,
are rejected -- all-or-nothing, never repaired or silently dropped."""

import pytest

from clinically_anchored_api.core.citations import (
    CitationError,
    SummaryLine,
    check_citations,
    parse_summary,
    verify_summary,
)

CLINIC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_CLINIC = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
PATIENT = "a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1"
OTHER_PATIENT = "c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1"
M1 = "00000000-0000-0000-0000-000000000001"
M2 = "00000000-0000-0000-0000-000000000002"
M3 = "00000000-0000-0000-0000-000000000003"
OTHER_PATIENT_MSG = "00000000-0000-0000-0000-0000000000a1"  # real, but another patient's
OTHER_CLINIC_MSG = "00000000-0000-0000-0000-0000000000b1"  # real, but another clinic's
NOWHERE = "00000000-0000-0000-0000-0000000000ff"  # exists nowhere


# --- parsing --------------------------------------------------------------------


def test_parses_cited_lines_in_the_supported_forms():
    raw = f"""- Reports the incision is red. [{M1}]
* Pain is improving [{M2}, {M3}]
1. Asked about showering [{M1}] [{M3}]
Plain line with no bullet [{M2}]
"""
    lines = parse_summary(raw)
    assert [ln.text for ln in lines] == [
        "Reports the incision is red.",
        "Pain is improving",
        "Asked about showering",
        "Plain line with no bullet",
    ]
    assert [ln.citations for ln in lines] == [(M1,), (M2, M3), (M1, M3), (M2,)]


def test_blank_lines_are_ignored_and_duplicate_ids_collapse():
    lines = parse_summary(f"\n- A claim [{M1}, {M1}] [{M1}]\n\n")
    assert lines == [SummaryLine("A claim", (M1,))]


def test_ids_are_canonicalised_so_case_cannot_cause_a_false_mismatch():
    lines = parse_summary("- Claim [ABCDEF00-0000-0000-0000-00000000ABCD]")
    assert lines[0].citations == ("abcdef00-0000-0000-0000-00000000abcd",)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("", "empty"),
        ("   \n\n", "empty"),
        (f"- Fine [{M1}]\n- A claim with no citation", "line 2 has no citation"),
        ("Here is the summary:", "no citation"),  # an intro line is an uncited line
        ("- Claim []", "empty citation"),
        ("- Claim [ , ]", "empty citation"),
        (f"[{M1}]", "only a citation"),
        (f"- See [{M1}] for more [{M2}]", "not a trailing citation"),
        (f"- Claim [{M1}] and then more text", "no citation"),
        ("- Claim [m3]", "not a message id"),
        ("- Claim [not-a-uuid]", "not a message id"),
        (f"- Claim [{M1}, nope]", "not a message id"),
    ],
)
def test_malformed_output_is_rejected_not_repaired(raw, reason):
    with pytest.raises(CitationError, match=reason):
        parse_summary(raw)


def test_one_bad_line_rejects_the_whole_summary():
    raw = f"- Good [{M1}]\n- Good [{M2}]\n- Bad, uncited\n- Good [{M3}]"
    with pytest.raises(CitationError):
        parse_summary(raw)


# --- the pure existence check ----------------------------------------------------


def test_check_passes_when_every_citation_is_in_the_thread():
    lines = [SummaryLine("a", (M1,)), SummaryLine("b", (M1, M2))]
    check_citations(lines, {M1, M2, M3})  # no exception


def test_check_reports_every_unknown_id_not_just_the_first():
    lines = [SummaryLine("a", (M1, NOWHERE)), SummaryLine("b", (OTHER_PATIENT_MSG,))]
    with pytest.raises(CitationError) as exc:
        check_citations(lines, {M1})
    assert exc.value.message_ids == (NOWHERE, OTHER_PATIENT_MSG)
    assert "not in this patient's thread" in exc.value.reason


# --- verification against the database -------------------------------------------


class _Query:
    def __init__(self, db):
        self.db, self._eq, self._in = db, [], None

    def select(self, *_a):
        return self

    def eq(self, col, val):
        self._eq.append((col, val))
        return self

    def in_(self, col, vals):
        self._in = (col, set(vals))
        return self

    def execute(self):
        self.db.queries += 1
        rows = [r for r in self.db.messages if all(r.get(c) == v for c, v in self._eq)]
        if self._in:
            rows = [r for r in rows if r[self._in[0]] in self._in[1]]
        return type("R", (), {"data": rows})()


class _Db:
    def __init__(self):
        self.queries = 0
        self.messages = [
            {"id": M1, "clinic_id": CLINIC, "patient_id": PATIENT},
            {"id": M2, "clinic_id": CLINIC, "patient_id": PATIENT},
            {"id": M3, "clinic_id": CLINIC, "patient_id": PATIENT},
            {"id": OTHER_PATIENT_MSG, "clinic_id": CLINIC, "patient_id": OTHER_PATIENT},
            {"id": OTHER_CLINIC_MSG, "clinic_id": OTHER_CLINIC, "patient_id": PATIENT},
        ]

    def table(self, name):
        assert name == "messages"
        return _Query(self)


def _verify(raw, db=None):
    return verify_summary(db or _Db(), clinic_id=CLINIC, patient_id=PATIENT, raw=raw)


def test_valid_citations_pass_and_lines_come_back_verified():
    lines = _verify(f"- First [{M1}]\n- Second [{M2}, {M3}]")
    assert [(ln.text, ln.citations) for ln in lines] == [("First", (M1,)), ("Second", (M2, M3))]


def test_citation_to_a_nonexistent_message_is_rejected():
    with pytest.raises(CitationError) as exc:
        _verify(f"- Real [{M1}]\n- Invented [{NOWHERE}]")
    assert exc.value.message_ids == (NOWHERE,)


def test_citation_to_another_patients_message_is_rejected():
    with pytest.raises(CitationError) as exc:
        _verify(f"- Leaks across patients [{OTHER_PATIENT_MSG}]")
    assert exc.value.message_ids == (OTHER_PATIENT_MSG,)


def test_citation_to_another_clinics_message_is_rejected():
    with pytest.raises(CitationError) as exc:
        _verify(f"- Leaks across clinics [{OTHER_CLINIC_MSG}]")
    assert exc.value.message_ids == (OTHER_CLINIC_MSG,)


def test_a_single_bad_citation_among_good_ones_fails_everything():
    # The good lines are not returned and the bad citation is not dropped.
    with pytest.raises(CitationError):
        _verify(f"- Good [{M1}]\n- Mixed [{M2}, {NOWHERE}]\n- Good [{M3}]")


def test_malformed_citations_never_reach_the_database():
    db = _Db()
    with pytest.raises(CitationError):
        _verify("- Claim [m3]", db)
    assert db.queries == 0
    _verify(f"- Claim [{M1}]", db)
    assert db.queries == 1


def test_uppercase_citations_still_verify_against_lowercase_ids():
    assert _verify(f"- Claim [{M1.upper()}]")[0].citations == (M1,)
