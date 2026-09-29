"""Clinician queue, check-in list and review: ordering, filtering, tenant
scoping, and the audited review write -- against an in-memory Supabase fake
that honours eq / is_ / in_ / order / limit / update."""

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.api import queue as queue_module
from clinically_anchored_api.core import auth as auth_module
from clinically_anchored_api.main import app

A, B = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
P1, P2, P3, PB = "p1", "p2", "p3", "pb"
USER = "99999999-9999-9999-9999-999999999999"
AUTH = {"Authorization": "Bearer good"}


class _Q:
    def __init__(self, rows):
        self.rows, self.f, self.order_by, self.lim, self.upd = rows, [], None, None, None

    def select(self, *_a):
        return self

    def eq(self, c, v):
        self.f.append(lambda r: r.get(c) == v)
        return self

    def is_(self, c, _null):
        self.f.append(lambda r: r.get(c) is None)
        return self

    def in_(self, c, vals):
        self.f.append(lambda r: r.get(c) in vals)
        return self

    def order(self, c, desc=False):
        self.order_by = (c, desc)
        return self

    def limit(self, n):
        self.lim = n
        return self

    def update(self, payload):
        self.upd = payload
        return self

    def execute(self):
        rows = [r for r in self.rows if all(f(r) for f in self.f)]
        if self.upd is not None:
            for r in rows:
                r.update(self.upd)
        if self.order_by:
            rows.sort(key=lambda r: r[self.order_by[0]], reverse=self.order_by[1])
        if self.lim:
            rows = rows[: self.lim]
        return type("R", (), {"data": rows})()


class _Fake:
    def __init__(self):
        def ci(id, pid, ts, flag=False, day=3, clinic=A, reviewed=None):
            return {"id": id, "clinic_id": clinic, "patient_id": pid, "procedure_id": "d1",
                    "post_op_day": day, "answers": {"fever": flag}, "is_red_flag": flag,
                    "created_at": ts, "reviewed_at": reviewed, "reviewed_by": None}

        def msg(id, pid, ts, sender="patient", read=None, clinic=A):
            return {"id": id, "clinic_id": clinic, "patient_id": pid, "sender": sender,
                    "read_at": read, "created_at": ts}

        self.t = {
            "clinic_members": [{"clinic_id": A, "user_id": USER, "role": "clinician"}],
            "patients": [
                {"id": P1, "clinic_id": A, "full_name": "Pat One"},
                {"id": P2, "clinic_id": A, "full_name": "Pat Two"},
                {"id": P3, "clinic_id": A, "full_name": "Pat Three"},
                {"id": PB, "clinic_id": B, "full_name": "Other Clinic"},
            ],
            "procedures": [{"id": "d1", "clinic_id": A, "name": "Hemorrhoidectomy"}],
            "check_ins": [
                ci("c1", P1, "2026-01-01T10:00:00+00:00"),                       # normal, old
                ci("c2", P2, "2026-01-01T09:00:00+00:00", flag=True),           # red flag, older
                ci("c3", P3, "2026-01-01T12:00:00+00:00", reviewed="2026-01-01T13:00:00+00:00"),
                ci("cb", PB, "2026-01-01T12:00:00+00:00", flag=True, clinic=B),
            ],
            "messages": [
                msg("m1", P1, "2026-01-01T11:00:00+00:00"),                      # unread from P1
                msg("m2", P3, "2026-01-01T14:00:00+00:00"),                      # unread, P3 only
                msg("m3", P1, "2026-01-01T15:00:00+00:00", sender="clinician"),  # not patient
                msg("m4", P2, "2026-01-01T08:00:00+00:00", read="2026-01-01T08:30:00+00:00"),
            ],
        }
        self.auth = type("A", (), {"get_user": lambda _s, _j: type(
            "U", (), {"user": type("X", (), {"id": USER})()})()})()

    def table(self, name):
        return _Q(self.t[name])


@pytest.fixture
def fake(monkeypatch):
    f = _Fake()
    audits: list[dict] = []
    f.audits = audits
    monkeypatch.setattr(queue_module, "get_supabase", lambda: f)
    monkeypatch.setattr(auth_module, "get_supabase", lambda: f)
    monkeypatch.setattr(queue_module, "record_event", lambda _db, **kw: audits.append(kw))
    return f


@pytest.fixture
def client(fake):
    return TestClient(app)


def test_queue_requires_membership(client):
    assert client.get(f"/clinics/{A}/queue").status_code == 401
    assert client.get(f"/clinics/{B}/queue", headers=AUTH).status_code == 403


def test_queue_puts_unreviewed_red_flags_first_then_latest_activity(client):
    items = client.get(f"/clinics/{A}/queue", headers=AUTH).json()
    # P2 has the red flag (even though its activity is oldest); then P3 (14:00 unread msg),
    # then P1 (15:00 clinician msg is ignored -> latest is 11:00 unread msg).
    assert [i["patient_id"] for i in items] == [P2, P3, P1]
    p2, p3, p1 = items
    assert p2["has_red_flag"] and p2["unreviewed_check_ins"] == 1 and p2["unread_messages"] == 0
    assert p2["latest_check_in_id"] == "c2" and p2["patient_name"] == "Pat Two"
    assert p3["unreviewed_check_ins"] == 0 and p3["unread_messages"] == 1
    assert p3["latest_check_in_id"] is None
    assert p1["unreviewed_check_ins"] == 1 and p1["unread_messages"] == 1
    assert p1["last_activity_at"] == "2026-01-01T11:00:00+00:00"


def test_queue_excludes_other_clinics(client):
    ids = [i["patient_id"] for i in client.get(f"/clinics/{A}/queue", headers=AUTH).json()]
    assert PB not in ids


def test_queue_drops_patients_once_handled(client, fake):
    fake.t["check_ins"][1]["reviewed_at"] = "2026-01-02T00:00:00+00:00"
    fake.t["messages"][1]["read_at"] = "2026-01-02T00:00:00+00:00"
    ids = [i["patient_id"] for i in client.get(f"/clinics/{A}/queue", headers=AUTH).json()]
    assert ids == [P1]


def test_list_check_ins_filters_and_resolves_names(client):
    r = client.get(f"/clinics/{A}/check-ins", headers=AUTH).json()
    assert [c["id"] for c in r] == ["c3", "c1", "c2"]  # newest first, clinic A only
    assert r[1]["patient_name"] == "Pat One" and r[1]["procedure_name"] == "Hemorrhoidectomy"

    flagged = client.get(f"/clinics/{A}/check-ins?red_flag_only=true", headers=AUTH).json()
    assert [c["id"] for c in flagged] == ["c2"]
    open_ = client.get(f"/clinics/{A}/check-ins?unreviewed_only=true", headers=AUTH).json()
    assert [c["id"] for c in open_] == ["c1", "c2"]
    one = client.get(f"/clinics/{A}/check-ins?patient_id={P1}&limit=1", headers=AUTH).json()
    assert [c["id"] for c in one] == ["c1"]


def test_list_limit_is_bounded(client):
    assert client.get(f"/clinics/{A}/check-ins?limit=0", headers=AUTH).status_code == 422
    assert client.get(f"/clinics/{A}/check-ins?limit=201", headers=AUTH).status_code == 422


def test_review_marks_reviewed_and_audits_once(client, fake):
    r = client.post(f"/clinics/{A}/check-ins/c2/review", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["reviewed_at"] is not None and body["reviewed_by"] == USER
    assert len(fake.audits) == 1
    assert fake.audits[0]["event_type"] == "check_in.reviewed"
    actor = fake.audits[0]["metadata"]["actor"]
    assert actor == {"type": "member", "id": USER, "role": "clinician"}

    again = client.post(f"/clinics/{A}/check-ins/c2/review", headers=AUTH)
    assert again.json()["reviewed_at"] == body["reviewed_at"]
    assert len(fake.audits) == 1  # idempotent: not audited twice


def test_review_other_clinics_check_in_is_404(client, fake):
    assert client.post(f"/clinics/{A}/check-ins/cb/review", headers=AUTH).status_code == 404
    assert fake.t["check_ins"][3]["reviewed_at"] is None
    assert fake.audits == []
