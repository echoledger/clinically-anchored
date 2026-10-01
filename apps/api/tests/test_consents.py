"""Consent records: generic storage + audit. Patients grant/withdraw through a
link token (either scope); clinicians read. Nothing here gates anything yet --
that decision (and the consent list/wording) is still open."""

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.api import consents as consents_module
from clinically_anchored_api.core import auth as auth_module
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.security import create_link_token
from clinically_anchored_api.main import app

CLINIC_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CLINIC_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
PATIENT_A = "a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1"
PATIENT_B = "b1b1b1b1-b1b1-b1b1-b1b1-b1b1b1b1b1b1"
USER = "99999999-9999-9999-9999-999999999999"
AUTH = {"Authorization": "Bearer good-jwt"}


class _Query:
    def __init__(self, db, rows):
        self.db, self.rows = db, rows
        self._eq: list[tuple[str, object]] = []
        self._null: list[str] = []
        self._insert = self._update = None
        self._desc = False

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._eq.append((col, val))
        return self

    def is_(self, col, val):
        assert val == "null"
        self._null.append(col)
        return self

    def order(self, _col, desc=False):
        self._desc = desc
        return self

    def insert(self, payload):
        self._insert = payload
        return self

    def update(self, payload):
        self._update = payload
        return self

    def execute(self):
        if self._insert is not None:
            if self.db.fail_next_insert_with:
                exc, self.db.fail_next_insert_with = self.db.fail_next_insert_with, None
                raise exc
            row = {"id": f"consent-{len(self.rows) + 1}", "revoked_at": None,
                   "granted_at": f"2026-01-01T00:00:{len(self.rows):02d}Z", **self._insert}
            # Mirror the partial unique index consents_one_active_grant.
            key = ("patient_id", "consent_type", "consent_text_version")
            if any(r["revoked_at"] is None and all(r[k] == row[k] for k in key) for r in self.rows):
                raise RuntimeError("duplicate key value violates unique constraint")
            self.rows.append(row)
            return type("R", (), {"data": [dict(row)]})()
        matched = [
            r for r in self.rows
            if all(r.get(c) == v for c, v in self._eq) and all(r.get(c) is None for c in self._null)
        ]
        if self._update is not None:
            for r in matched:
                r.update(self._update)
        matched.sort(key=lambda r: r.get("granted_at", ""), reverse=self._desc)
        return type("R", (), {"data": [dict(r) for r in matched]})()


class _FakeAuth:
    def get_user(self, jwt):
        if jwt != "good-jwt":
            raise ValueError("bad jwt")
        return type("UR", (), {"user": type("U", (), {"id": USER})()})()


class _FakeSupabase:
    def __init__(self):
        self.auth = _FakeAuth()
        self.tables = {
            "clinic_members": [{"clinic_id": CLINIC_A, "user_id": USER, "role": "delegate"}],
            "patients": [
                {"id": PATIENT_A, "clinic_id": CLINIC_A},
                {"id": PATIENT_B, "clinic_id": CLINIC_B},
            ],
            "consents": [],
        }
        self.fail_next_insert_with = None

    def table(self, name):
        return _Query(self, self.tables[name])


@pytest.fixture
def audit_calls(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(consents_module, "record_event", lambda _db, **kw: calls.append(kw))
    return calls


@pytest.fixture
def fake(monkeypatch, audit_calls):
    fake = _FakeSupabase()
    monkeypatch.setattr(consents_module, "get_supabase", lambda: fake)
    monkeypatch.setattr(auth_module, "get_supabase", lambda: fake)
    return fake


@pytest.fixture
def client(fake):
    return TestClient(app)


def _tok(scope="messages", clinic=CLINIC_A, patient=PATIENT_A):
    return {"token": create_link_token(clinic_id=clinic, patient_id=patient, scope=scope)}


def _grant(client, ctype="messaging", version="v1", **tok):
    return client.post(
        "/consents",
        params=tok or _tok(),
        json={"consent_type": ctype, "consent_text_version": version},
    )


# --- grant --------------------------------------------------------------------


def test_grant_stores_and_audits(client, fake, audit_calls):
    r = _grant(client)
    assert r.status_code == 200
    assert r.json()["active"] == ["messaging"]

    row = fake.tables["consents"][0]
    assert (row["clinic_id"], row["patient_id"]) == (CLINIC_A, PATIENT_A)
    assert (row["consent_type"], row["consent_text_version"]) == ("messaging", "v1")
    assert row["revoked_at"] is None

    assert len(audit_calls) == 1
    call = audit_calls[0]
    assert call["event_type"] == "consent.granted"
    assert call["clinic_id"] == CLINIC_A
    assert call["payload"]["consent_id"] == row["id"]
    assert call["payload"]["consent_type"] == "messaging"
    assert call["payload"]["consent_text_version"] == "v1"
    assert call["metadata"] == {
        "ref_type": "consent",
        "ref_id": row["id"],
        "actor": {"type": "patient", "id": PATIENT_A},
    }


def test_grant_works_with_either_link_scope(client, fake):
    assert _grant(client, **_tok("messages")).status_code == 200
    assert _grant(client, ctype="other_thing", **_tok("checkin")).status_code == 200
    assert {r["consent_type"] for r in fake.tables["consents"]} == {"messaging", "other_thing"}


def test_grant_identity_comes_from_token_not_body(client, fake):
    client.post(
        "/consents",
        params=_tok(),
        json={
            "consent_type": "messaging",
            "consent_text_version": "v1",
            "patient_id": PATIENT_B,
            "clinic_id": CLINIC_B,
        },
    )
    assert (fake.tables["consents"][0]["clinic_id"], fake.tables["consents"][0]["patient_id"]) == (
        CLINIC_A,
        PATIENT_A,
    )


def test_repeated_grant_is_idempotent_and_audited_once(client, fake, audit_calls):
    _grant(client)
    r = _grant(client)
    assert r.status_code == 200
    assert len(fake.tables["consents"]) == 1
    assert len(audit_calls) == 1


def test_new_wording_version_is_a_new_audited_grant(client, fake, audit_calls):
    _grant(client, version="v1")
    r = _grant(client, version="v2")
    assert len(fake.tables["consents"]) == 2
    assert [c["payload"]["consent_text_version"] for c in audit_calls] == ["v1", "v2"]
    assert r.json()["active"] == ["messaging"]


def test_concurrent_duplicate_grant_is_treated_as_idempotent(
    client, fake, audit_calls, monkeypatch
):
    # Our pre-check saw nothing, then another request's insert won the unique
    # index before ours: the insert raises, a re-check finds the winner's row.
    winner = {"id": "consent-w", "clinic_id": CLINIC_A, "patient_id": PATIENT_A,
              "consent_type": "messaging", "consent_text_version": "v1",
              "granted_at": "2026-01-01T00:00:00Z", "revoked_at": None}
    seen = {"n": 0}
    real_execute = _Query.execute

    def racy_execute(self):
        # First live-grant lookup comes back empty even though the row exists.
        if self._null == ["revoked_at"] and self._update is None and self._insert is None:
            seen["n"] += 1
            if seen["n"] == 1:
                fake.tables["consents"].append(winner)
                return type("R", (), {"data": []})()
        return real_execute(self)

    monkeypatch.setattr(_Query, "execute", racy_execute)
    r = _grant(client)
    assert r.status_code == 200
    assert len(fake.tables["consents"]) == 1
    assert audit_calls == []  # the winning request audits its own insert


def test_genuine_insert_failure_is_not_swallowed(client, fake):
    fake.fail_next_insert_with = RuntimeError("database down")
    with pytest.raises(RuntimeError, match="database down"):
        _grant(client)


@pytest.mark.parametrize(
    "body",
    [
        {"consent_type": "Not A Slug", "consent_text_version": "v1"},
        {"consent_type": "", "consent_text_version": "v1"},
        {"consent_type": "messaging", "consent_text_version": ""},
        {"consent_type": "messaging", "consent_text_version": "has spaces"},
        {"consent_type": "messaging"},
    ],
)
def test_grant_rejects_malformed_input(client, fake, body):
    assert client.post("/consents", params=_tok(), json=body).status_code == 422
    assert fake.tables["consents"] == []


def test_allowlist_unset_accepts_any_slug_and_set_rejects_others(client, fake, monkeypatch):
    assert _grant(client, ctype="anything_goes").status_code == 200
    monkeypatch.setattr(get_settings(), "consent_types", "messaging, ai_assisted")
    assert _grant(client, ctype="ai_assisted").status_code == 200
    r = _grant(client, ctype="anything_goes", version="v2")
    assert r.status_code == 422
    assert r.json()["detail"] == "Unknown consent type."


def test_grant_reports_500_when_audit_write_fails(client, monkeypatch):
    def boom(_db, **_kw):
        raise consents_module.AuditWriteError("down")

    monkeypatch.setattr(consents_module, "record_event", boom)
    r = _grant(client)
    assert r.status_code == 500
    assert "not audited" in r.json()["detail"]


def test_grant_requires_a_valid_token(client):
    assert client.post("/consents", params={"token": "garbage"}, json={}).status_code == 401
    assert client.get("/consents", params={"token": "garbage"}).status_code == 401
    assert client.post("/consents/messaging/revoke", params={"token": "x"}).status_code == 401


def test_grant_for_patient_not_in_clinic_is_404(client):
    r = _grant(client, **_tok(clinic=CLINIC_A, patient=PATIENT_B))
    assert r.status_code == 404


# --- withdraw -----------------------------------------------------------------


def test_revoke_ends_grant_keeps_history_and_audits(client, fake, audit_calls):
    _grant(client)
    audit_calls.clear()
    r = client.post("/consents/messaging/revoke", params=_tok())
    assert r.status_code == 200
    body = r.json()
    assert body["active"] == []
    assert len(body["records"]) == 1 and body["records"][0]["revoked_at"] is not None

    assert len(audit_calls) == 1
    call = audit_calls[0]
    assert call["event_type"] == "consent.revoked"
    assert call["payload"]["revoked_at"] == fake.tables["consents"][0]["revoked_at"]
    assert call["metadata"]["ref_type"] == "consent"
    assert call["metadata"]["ref_id"] == fake.tables["consents"][0]["id"]
    assert call["metadata"]["actor"] == {"type": "patient", "id": PATIENT_A}


def test_revoke_ends_every_live_version_and_audits_each(client, fake, audit_calls):
    _grant(client, version="v1")
    _grant(client, version="v2")
    audit_calls.clear()
    client.post("/consents/messaging/revoke", params=_tok())
    assert all(r["revoked_at"] for r in fake.tables["consents"])
    assert sorted(c["metadata"]["ref_id"] for c in audit_calls) == ["consent-1", "consent-2"]


def test_revoke_is_idempotent_and_only_touches_that_type(client, fake, audit_calls):
    _grant(client, ctype="messaging")
    _grant(client, ctype="ai_assisted")
    client.post("/consents/messaging/revoke", params=_tok())
    audit_calls.clear()
    r = client.post("/consents/messaging/revoke", params=_tok())
    assert r.status_code == 200
    assert audit_calls == []
    assert r.json()["active"] == ["ai_assisted"]


def test_revoke_with_nothing_granted_is_a_quiet_noop(client, audit_calls):
    r = client.post("/consents/messaging/revoke", params=_tok())
    assert r.status_code == 200 and r.json()["active"] == []
    assert audit_calls == []


def test_regrant_after_revoke_is_a_new_row(client, fake, audit_calls):
    _grant(client)
    client.post("/consents/messaging/revoke", params=_tok())
    r = _grant(client)
    assert r.json()["active"] == ["messaging"]
    assert len(fake.tables["consents"]) == 2
    assert [c["event_type"] for c in audit_calls] == [
        "consent.granted", "consent.revoked", "consent.granted",
    ]


def test_revoke_rejects_malformed_type(client):
    assert client.post("/consents/NOT-A-SLUG/revoke", params=_tok()).status_code == 422


# --- reads --------------------------------------------------------------------


def test_patient_reads_only_their_own_consents(client, fake):
    _grant(client)
    _grant(client, **_tok(clinic=CLINIC_B, patient=PATIENT_B))
    r = client.get("/consents", params=_tok())
    assert r.json()["patient_id"] == PATIENT_A
    assert [c["consent_type"] for c in r.json()["records"]] == ["messaging"]


def test_clinician_reads_patient_consent_status(client):
    _grant(client, ctype="messaging")
    _grant(client, ctype="ai_assisted")
    client.post("/consents/ai_assisted/revoke", params=_tok())
    r = client.get(f"/clinics/{CLINIC_A}/patients/{PATIENT_A}/consents", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["active"] == ["messaging"]
    assert {c["consent_type"]: c["revoked_at"] is not None for c in body["records"]} == {
        "messaging": False,
        "ai_assisted": True,
    }


def test_clinician_read_requires_membership_and_clinic_scoping(client):
    url = f"/clinics/{CLINIC_A}/patients/{PATIENT_A}/consents"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer nope"}).status_code == 401
    # Not a member of clinic B.
    other_clinic = f"/clinics/{CLINIC_B}/patients/{PATIENT_B}/consents"
    assert client.get(other_clinic, headers=AUTH).status_code == 403
    # Member of A, but the patient belongs to B.
    cross = f"/clinics/{CLINIC_A}/patients/{PATIENT_B}/consents"
    assert client.get(cross, headers=AUTH).status_code == 404
