"""Per-patient rate limits on link-token routes: the sliding window itself,
and how it's wired (verified tokens only, separate read/write budgets,
per-patient isolation, 429 + Retry-After)."""

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.api import messages as messages_module
from clinically_anchored_api.core import ratelimit
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.security import create_link_token
from clinically_anchored_api.main import app

CLINIC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PATIENT = "a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1"
OTHER = "b1b1b1b1-b1b1-b1b1-b1b1-b1b1b1b1b1b1"


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(ratelimit, "_clock", clock)
    return clock


# --- the window ---------------------------------------------------------------


def test_allows_up_to_limit_then_reports_wait(clock):
    assert [ratelimit.hit(("k",), 3, 60) for _ in range(3)] == [0, 0, 0]
    assert ratelimit.hit(("k",), 3, 60) == 60
    clock.now += 45
    assert ratelimit.hit(("k",), 3, 60) == 15  # oldest hit leaves in 15s


def test_window_slides_rather_than_resetting_all_at_once(clock):
    ratelimit.hit(("k",), 2, 60)
    clock.now += 40
    ratelimit.hit(("k",), 2, 60)
    assert ratelimit.hit(("k",), 2, 60) > 0
    clock.now += 21  # first hit is now 61s old, second only 21s
    assert ratelimit.hit(("k",), 2, 60) == 0
    assert ratelimit.hit(("k",), 2, 60) > 0


def test_rejected_requests_are_not_counted(clock):
    ratelimit.hit(("k",), 1, 60)
    for _ in range(50):
        assert ratelimit.hit(("k",), 1, 60) > 0
    clock.now += 60
    assert ratelimit.hit(("k",), 1, 60) == 0  # hammering didn't extend the block


def test_keys_are_independent(clock):
    assert ratelimit.hit(("a",), 1, 60) == 0
    assert ratelimit.hit(("b",), 1, 60) == 0
    assert ratelimit.hit(("a",), 1, 60) > 0


def test_expired_keys_are_swept(clock, monkeypatch):
    monkeypatch.setattr(ratelimit, "_MAX_KEYS", 5)
    for i in range(6):
        ratelimit.hit((i,), 1, 60)
    clock.now += 61
    ratelimit.hit(("fresh",), 1, 60)
    assert list(ratelimit._hits) == [("fresh",)]


# --- wiring -------------------------------------------------------------------


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def insert(self, payload):
        self.rows = [{"id": "m1", "read_at": None, "created_at": "2026-01-01T00:00:00Z", **payload}]
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()


class _Db:
    def table(self, name):
        return _Query([{"id": PATIENT}] if name == "patients" else [])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(messages_module, "get_supabase", lambda: _Db())
    monkeypatch.setattr(messages_module, "record_event", lambda *_a, **_k: None)
    settings = get_settings()
    monkeypatch.setattr(settings, "patient_link_reads_per_minute", 3)
    monkeypatch.setattr(settings, "patient_link_writes_per_minute", 2)
    return TestClient(app)


def _tok(patient=PATIENT, scope="messages"):
    return create_link_token(clinic_id=CLINIC, patient_id=patient, scope=scope)


def test_reads_are_throttled_with_retry_after(client, clock):
    params = {"token": _tok()}
    assert [client.get("/messages", params=params).status_code for _ in range(3)] == [200] * 3
    r = client.get("/messages", params=params)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) == 60
    clock.now += 61
    assert client.get("/messages", params=params).status_code == 200


def test_writes_have_their_own_stricter_budget(client):
    params = {"token": _tok()}
    codes = [
        client.post("/messages", params=params, json={"body": "hi"}).status_code for _ in range(3)
    ]
    assert codes == [200, 200, 429]
    assert client.get("/messages", params=params).status_code == 200  # reads unaffected


def test_budget_is_per_patient_not_global(client):
    for _ in range(3):
        client.get("/messages", params={"token": _tok()})
    assert client.get("/messages", params={"token": _tok()}).status_code == 429
    assert client.get("/messages", params={"token": _tok(patient=OTHER)}).status_code != 429


def test_budget_is_shared_across_link_scopes_for_one_patient(client):
    # Two valid tokens for the same patient don't double the allowance.
    for _ in range(2):
        client.post("/messages", params={"token": _tok()}, json={"body": "x"})
    r = client.post("/check-ins", params={"token": _tok(scope="checkin")}, json={"answers": {}})
    assert r.status_code == 429


def test_invalid_tokens_are_not_counted(client):
    for _ in range(10):
        assert client.get("/messages", params={"token": "garbage"}).status_code == 401
    assert client.get("/messages", params={"token": _tok()}).status_code == 200


def test_zero_disables_a_budget(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "patient_link_reads_per_minute", 0)
    params = {"token": _tok()}
    assert all(client.get("/messages", params=params).status_code == 200 for _ in range(20))
