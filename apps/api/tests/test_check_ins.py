"""Tests the check-in submission flow: token verification, red-flag
evaluation, and the write path -- with the Supabase client replaced by a
fake so these tests don't touch a real network or database."""

from fastapi.testclient import TestClient

from clinically_anchored_api.api import check_ins as check_ins_module
from clinically_anchored_api.core.security import create_checkin_token
from clinically_anchored_api.main import app

CLINIC_ID = "11111111-1111-1111-1111-111111111111"
PATIENT_ID = "22222222-2222-2222-2222-222222222222"


class _FakeQuery:
    def __init__(self, table: "_FakeTable"):
        self._table = table
        self._insert_payload: dict | None = None

    def insert(self, payload: dict) -> "_FakeQuery":
        self._insert_payload = payload
        return self

    def select(self, *_args, **_kwargs) -> "_FakeQuery":
        return self

    def eq(self, *_args, **_kwargs) -> "_FakeQuery":
        return self

    def order(self, *_args, **_kwargs) -> "_FakeQuery":
        return self

    def execute(self):
        if self._insert_payload is not None:
            row = {
                "id": "33333333-3333-3333-3333-333333333333",
                "created_at": "2026-01-01T00:00:00Z",
                **self._insert_payload,
            }
            self._table.inserted_rows.append(row)
            return type("Result", (), {"data": [row]})()
        # A select() query (e.g. procedures lookup) -- return the table's
        # canned rows, if the test set any.
        return type("Result", (), {"data": self._table.select_rows})()


class _FakeTable:
    def __init__(self, select_rows: list[dict] | None = None):
        self.inserted_rows: list[dict] = []
        self.select_rows = select_rows or []

    def __call__(self, _name: str) -> _FakeQuery:
        return _FakeQuery(self)


class _FakeSupabase:
    def __init__(self, select_rows: list[dict] | None = None):
        self.table = _FakeTable(select_rows)


def _client_with_fake_db(fake: _FakeSupabase) -> TestClient:
    # Patch where the name is *used* (check_ins.py imported it into its own
    # namespace), not where it's defined -- patching core.db.get_supabase
    # would miss the already-bound reference in check_ins.py.
    original = check_ins_module.get_supabase
    check_ins_module.get_supabase = lambda: fake  # type: ignore[assignment]
    client = TestClient(app)
    client._fake_db_restore = original  # stash for teardown
    return client


def test_submit_check_in_writes_row_and_flags_red_flag():
    fake = _FakeSupabase()
    client = _client_with_fake_db(fake)
    try:
        token = create_checkin_token(clinic_id=CLINIC_ID, patient_id=PATIENT_ID)

        response = client.post(
            "/check-ins",
            params={"token": token},
            json={"post_op_day": 5, "answers": {"fever": True}},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["clinic_id"] == CLINIC_ID
        assert body["patient_id"] == PATIENT_ID
        assert body["is_red_flag"] is True
        assert len(fake.table.inserted_rows) == 1
    finally:
        check_ins_module.get_supabase = client._fake_db_restore


def test_submit_check_in_no_red_flags():
    fake = _FakeSupabase()
    client = _client_with_fake_db(fake)
    try:
        token = create_checkin_token(clinic_id=CLINIC_ID, patient_id=PATIENT_ID)

        response = client.post(
            "/check-ins",
            params={"token": token},
            json={"post_op_day": 5, "answers": {"fever": False}},
        )

        assert response.status_code == 200
        assert response.json()["is_red_flag"] is False
    finally:
        check_ins_module.get_supabase = client._fake_db_restore


def test_submit_check_in_rejects_bad_token():
    fake = _FakeSupabase()
    client = _client_with_fake_db(fake)
    try:
        response = client.post(
            "/check-ins",
            params={"token": "not-a-real-token"},
            json={"answers": {}},
        )
        assert response.status_code == 401
    finally:
        check_ins_module.get_supabase = client._fake_db_restore


def test_check_in_context_resolves_clinic_and_procedures():
    fake = _FakeSupabase(
        select_rows=[{"id": "44444444-4444-4444-4444-444444444444", "name": "Hemorrhoidectomy"}]
    )
    client = _client_with_fake_db(fake)
    try:
        token = create_checkin_token(clinic_id=CLINIC_ID, patient_id=PATIENT_ID)

        response = client.get("/check-ins/context", params={"token": token})

        assert response.status_code == 200
        body = response.json()
        assert body["clinic_id"] == CLINIC_ID
        assert body["procedures"][0]["name"] == "Hemorrhoidectomy"
    finally:
        check_ins_module.get_supabase = client._fake_db_restore


def test_check_in_context_rejects_bad_token():
    fake = _FakeSupabase()
    client = _client_with_fake_db(fake)
    try:
        response = client.get("/check-ins/context", params={"token": "garbage"})
        assert response.status_code == 401
    finally:
        check_ins_module.get_supabase = client._fake_db_restore


def test_submit_check_in_rejects_procedure_from_another_clinic():
    # Fake returns no rows for the (procedure id, clinic id) lookup.
    fake = _FakeSupabase(select_rows=[])
    client = _client_with_fake_db(fake)
    try:
        token = create_checkin_token(clinic_id=CLINIC_ID, patient_id=PATIENT_ID)
        response = client.post(
            "/check-ins",
            params={"token": token},
            json={"procedure_id": "55555555-5555-5555-5555-555555555555", "answers": {}},
        )
        assert response.status_code == 422
        assert fake.table.inserted_rows == []
    finally:
        check_ins_module.get_supabase = client._fake_db_restore


def test_submit_check_in_accepts_procedure_in_clinic():
    fake = _FakeSupabase(select_rows=[{"id": "55555555-5555-5555-5555-555555555555"}])
    client = _client_with_fake_db(fake)
    try:
        token = create_checkin_token(clinic_id=CLINIC_ID, patient_id=PATIENT_ID)
        response = client.post(
            "/check-ins",
            params={"token": token},
            json={"procedure_id": "55555555-5555-5555-5555-555555555555", "answers": {}},
        )
        assert response.status_code == 200
    finally:
        check_ins_module.get_supabase = client._fake_db_restore
