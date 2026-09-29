"""Clinician-side message endpoints: auth, tenant scoping, and the write
path, against an in-memory fake Supabase that honours eq() filters (so
cross-clinic checks are actually exercised, not stubbed away)."""

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.api import messages as messages_module
from clinically_anchored_api.core import auth as auth_module
from clinically_anchored_api.main import app

CLINIC_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CLINIC_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
PATIENT_A = "a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1"
PATIENT_B = "b1b1b1b1-b1b1-b1b1-b1b1-b1b1b1b1b1b1"
USER = "99999999-9999-9999-9999-999999999999"
AUTH = {"Authorization": "Bearer good-jwt"}


class _Query:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self._filters: list[tuple[str, object]] = []
        self._insert: dict | None = None
        self._update: dict | None = None
        self._order: str | None = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, col, **_k):
        self._order = col
        return self

    def insert(self, payload):
        self._insert = payload
        return self

    def update(self, payload):
        self._update = payload
        return self

    def _matching(self):
        return [r for r in self._rows if all(r.get(c) == v for c, v in self._filters)]

    def execute(self):
        if self._insert is not None:
            row = {
                "id": f"msg-{len(self._rows) + 1}",
                "read_at": None,
                "created_at": f"2026-01-01T00:00:0{len(self._rows)}Z",
                **self._insert,
            }
            self._rows.append(row)
            return type("R", (), {"data": [row]})()
        matched = self._matching()
        if self._update is not None:
            for r in matched:
                r.update(self._update)
        if self._order:
            matched.sort(key=lambda r: r[self._order])
        return type("R", (), {"data": matched})()


class _FakeAuth:
    def get_user(self, jwt):
        if jwt != "good-jwt":
            raise ValueError("bad jwt")
        return type("UR", (), {"user": type("U", (), {"id": USER})()})()


class _FakeSupabase:
    def __init__(self):
        self.tables = {
            "clinic_members": [{"clinic_id": CLINIC_A, "user_id": USER, "role": "clinician"}],
            "patients": [
                {"id": PATIENT_A, "clinic_id": CLINIC_A},
                {"id": PATIENT_B, "clinic_id": CLINIC_B},
            ],
            "messages": [],
        }
        self.auth = _FakeAuth()

    def table(self, name):
        return _Query(self.tables[name])


@pytest.fixture
def fake(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(messages_module, "get_supabase", lambda: fake)
    monkeypatch.setattr(auth_module, "get_supabase", lambda: fake)
    return fake


@pytest.fixture
def client(fake):
    return TestClient(app)


def _thread(clinic, patient):
    return f"/clinics/{clinic}/patients/{patient}/messages"


def test_requires_bearer_token(client):
    assert client.get(_thread(CLINIC_A, PATIENT_A)).status_code == 401


def test_rejects_invalid_jwt(client):
    r = client.get(_thread(CLINIC_A, PATIENT_A), headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_rejects_non_member_of_clinic(client):
    r = client.get(_thread(CLINIC_B, PATIENT_B), headers=AUTH)
    assert r.status_code == 403


def test_patient_from_another_clinic_is_404(client):
    # Member of A, addressing A's URL with B's patient id.
    r = client.get(_thread(CLINIC_A, PATIENT_B), headers=AUTH)
    assert r.status_code == 404
    r = client.post(_thread(CLINIC_A, PATIENT_B), headers=AUTH, json={"body": "hi"})
    assert r.status_code == 404


def test_send_then_list_thread(client, fake):
    r = client.post(_thread(CLINIC_A, PATIENT_A), headers=AUTH, json={"body": "How is the pain?"})
    assert r.status_code == 200
    assert r.json()["sender"] == "clinician"
    assert fake.tables["messages"][0]["clinic_id"] == CLINIC_A

    r = client.get(_thread(CLINIC_A, PATIENT_A), headers=AUTH)
    assert [m["body"] for m in r.json()] == ["How is the pain?"]


def test_send_ignores_client_supplied_sender(client, fake):
    client.post(
        _thread(CLINIC_A, PATIENT_A), headers=AUTH, json={"body": "x", "sender": "patient"}
    )
    assert fake.tables["messages"][0]["sender"] == "clinician"


def test_send_rejects_empty_body(client):
    r = client.post(_thread(CLINIC_A, PATIENT_A), headers=AUTH, json={"body": ""})
    assert r.status_code == 422


def test_mark_read_only_touches_patient_messages_and_is_idempotent(client, fake):
    fake.tables["messages"] += [
        {"id": "p1", "clinic_id": CLINIC_A, "patient_id": PATIENT_A, "sender": "patient",
         "body": "ok", "read_at": None, "created_at": "2026-01-01T00:00:00Z"},
        {"id": "c1", "clinic_id": CLINIC_A, "patient_id": PATIENT_A, "sender": "clinician",
         "body": "hi", "read_at": None, "created_at": "2026-01-01T00:00:01Z"},
    ]
    first = client.post(f"/clinics/{CLINIC_A}/messages/p1/read", headers=AUTH).json()
    assert first["read_at"] is not None
    again = client.post(f"/clinics/{CLINIC_A}/messages/p1/read", headers=AUTH).json()
    assert again["read_at"] == first["read_at"]

    own = client.post(f"/clinics/{CLINIC_A}/messages/c1/read", headers=AUTH).json()
    assert own["read_at"] is None


def test_mark_read_other_clinic_message_is_404(client, fake):
    fake.tables["messages"].append(
        {"id": "pb", "clinic_id": CLINIC_B, "patient_id": PATIENT_B, "sender": "patient",
         "body": "x", "read_at": None, "created_at": "2026-01-01T00:00:00Z"}
    )
    r = client.post(f"/clinics/{CLINIC_A}/messages/pb/read", headers=AUTH)
    assert r.status_code == 404
