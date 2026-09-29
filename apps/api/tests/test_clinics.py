"""/me, patient list/create, and link issuance: auth, tenant scoping, audit,
and that an issued link actually works against the patient-facing routes."""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.api import clinics as clinics_module
from clinically_anchored_api.core import auth as auth_module
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.security import verify_link_token
from clinically_anchored_api.main import app

A, B = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
USER = "99999999-9999-9999-9999-999999999999"
AUTH = {"Authorization": "Bearer good"}


class _Q:
    def __init__(self, rows):
        self.rows, self.f, self.ins, self.order_col = rows, [], None, None

    def select(self, *_a):
        return self

    def eq(self, c, v):
        self.f.append(lambda r: r.get(c) == v)
        return self

    def in_(self, c, vals):
        self.f.append(lambda r: r.get(c) in vals)
        return self

    def order(self, c, **_k):
        self.order_col = c
        return self

    def insert(self, payload):
        self.ins = payload
        return self

    def execute(self):
        if self.ins is not None:
            row = {"id": f"pat-{len(self.rows) + 1}", "created_at": "2026-01-01T00:00:00Z",
                   **self.ins}
            self.rows.append(row)
            return type("R", (), {"data": [row]})()
        rows = [r for r in self.rows if all(f(r) for f in self.f)]
        if self.order_col:
            rows.sort(key=lambda r: r[self.order_col])
        return type("R", (), {"data": rows})()


class _Fake:
    def __init__(self):
        self.t = {
            "clinic_members": [{"clinic_id": A, "user_id": USER, "role": "owner"}],
            "clinics": [{"id": A, "name": "Clinic A"}, {"id": B, "name": "Clinic B"}],
            "patients": [
                {"id": "p1", "clinic_id": A, "full_name": "Zed", "contact": None,
                 "created_at": "2026-01-01T00:00:00Z"},
                {"id": "p0", "clinic_id": A, "full_name": "Amy", "contact": "amy@example.com",
                 "created_at": "2026-01-01T00:00:00Z"},
                {"id": "pb", "clinic_id": B, "full_name": "Other", "contact": None,
                 "created_at": "2026-01-01T00:00:00Z"},
            ],
        }
        user = type("X", (), {"id": USER, "email": "doc@example.com"})()
        self.auth = type("A", (), {"get_user": lambda _s, _j: type("U", (), {"user": user})()})()

    def table(self, name):
        return _Q(self.t[name])


@pytest.fixture
def fake(monkeypatch):
    f = _Fake()
    f.audits = []
    monkeypatch.setattr(clinics_module, "get_supabase", lambda: f)
    monkeypatch.setattr(auth_module, "get_supabase", lambda: f)
    monkeypatch.setattr(clinics_module, "record_event", lambda _db, **kw: f.audits.append(kw))
    monkeypatch.setattr(get_settings(), "web_base_url", "https://app.example.com/")
    return f


@pytest.fixture
def client(fake):
    return TestClient(app)


def test_me_lists_only_my_clinics(client):
    body = client.get("/me", headers=AUTH).json()
    assert body["email"] == "doc@example.com"
    assert body["clinics"] == [{"id": A, "name": "Clinic A", "role": "owner"}]


def test_me_requires_auth(client):
    assert client.get("/me").status_code == 401


def test_patient_endpoints_are_tenant_scoped(client):
    assert client.get(f"/clinics/{B}/patients", headers=AUTH).status_code == 403
    create = client.post(f"/clinics/{B}/patients", headers=AUTH, json={"full_name": "x"})
    assert create.status_code == 403
    link = client.post(f"/clinics/{B}/patients/pb/links/messages", headers=AUTH)
    assert link.status_code == 403


def test_list_patients_sorted_by_name_and_clinic_only(client):
    names = [p["full_name"] for p in client.get(f"/clinics/{A}/patients", headers=AUTH).json()]
    assert names == ["Amy", "Zed"]


def test_create_patient_saves_trimmed_and_audits_without_pii_in_metadata(client, fake):
    r = client.post(
        f"/clinics/{A}/patients",
        headers=AUTH,
        json={"full_name": "  Test Person ", "contact": " 555-0100 "},
    )
    assert r.status_code == 200
    assert (r.json()["full_name"], r.json()["contact"]) == ("Test Person", "555-0100")
    assert len(fake.audits) == 1
    audit = fake.audits[0]
    assert audit["event_type"] == "patient.created"
    assert "Test Person" not in str(audit["metadata"]) and "555-0100" not in str(audit["metadata"])
    assert audit["metadata"]["actor"]["role"] == "owner"


def test_create_patient_validates_name(client):
    r = client.post(f"/clinics/{A}/patients", headers=AUTH, json={"full_name": ""})
    assert r.status_code == 422


@pytest.mark.parametrize("scope", ["checkin", "messages"])
def test_issued_link_is_valid_for_that_patient_and_scope(client, fake, scope):
    r = client.post(f"/clinics/{A}/patients/p1/links/{scope}", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    url = urlparse(body["url"])
    assert url.netloc == "app.example.com"
    assert url.path == ("/check-in" if scope == "checkin" else "/messages")
    token = parse_qs(url.query)["token"][0]
    assert verify_link_token(token, scope) == {"clinic_id": A, "patient_id": "p1"}
    # Audited without leaking the token.
    assert fake.audits[0]["event_type"] == "link.issued"
    assert token not in str(fake.audits[0])
    assert fake.audits[0]["metadata"]["scope"] == scope


def test_link_for_patient_in_another_clinic_is_404(client, fake):
    assert client.post(f"/clinics/{A}/patients/pb/links/messages", headers=AUTH).status_code == 404
    assert fake.audits == []


def test_unknown_link_scope_is_404(client):
    assert client.post(f"/clinics/{A}/patients/p1/links/admin", headers=AUTH).status_code == 404


def test_link_refuses_outside_development_without_web_base_url(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "web_base_url", "")
    monkeypatch.setattr(settings, "environment", "production")
    r = client.post(f"/clinics/{A}/patients/p1/links/messages", headers=AUTH)
    assert r.status_code == 503
