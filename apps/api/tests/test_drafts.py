"""AI message drafts: generate, then a clinician approves / edits / rejects. Against an
in-memory fake that honours eq() filters and emulates the `decide_message_draft`
database function (the real SQL is exercised against Postgres separately), with the
model call replaced so no AWS is touched."""

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.api import drafts as drafts_module
from clinically_anchored_api.api import messages as messages_module
from clinically_anchored_api.core import ai
from clinically_anchored_api.core import auth as auth_module
from clinically_anchored_api.main import app

CLINIC_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CLINIC_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
PATIENT_A = "a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1"
PATIENT_B = "b1b1b1b1-b1b1-b1b1-b1b1-b1b1b1b1b1b1"
DOCTOR = "99999999-9999-9999-9999-999999999999"
SECRETARY = "77777777-7777-7777-7777-777777777777"
AUTH = {"Authorization": "Bearer doctor"}
SECRETARY_AUTH = {"Authorization": "Bearer secretary"}
MODEL = "test.model-v1"


class _Query:
    def __init__(self, db, name):
        self.db, self.name, self.rows = db, name, db.tables[name]
        self._eq: list[tuple[str, object]] = []
        self._order = None
        self._desc = False
        self._limit = None
        self._insert = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._eq.append((col, val))
        return self

    def order(self, col, desc=False):
        self._order, self._desc = col, desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def insert(self, payload):
        self._insert = payload
        return self

    def execute(self):
        if self._insert is not None:
            return type("R", (), {"data": [self.db.insert(self.name, self._insert)]})()
        rows = [dict(r) for r in self.rows if all(r.get(c) == v for c, v in self._eq)]
        if self._order:
            rows.sort(key=lambda r: r[self._order], reverse=self._desc)
        if self._limit:
            rows = rows[: self._limit]
        return type("R", (), {"data": rows})()


class _Rpc:
    def __init__(self, db, params):
        self.db, self.params = db, params

    def execute(self):
        return type("R", (), {"data": self.db.decide(**self.params)})()


class _FakeAuth:
    def get_user(self, jwt):
        users = {"doctor": DOCTOR, "secretary": SECRETARY}
        if jwt not in users:
            raise ValueError("bad jwt")
        return type("UR", (), {"user": type("U", (), {"id": users[jwt]})()})()


class _FakeSupabase:
    def __init__(self):
        self.auth = _FakeAuth()
        self.tables = {
            "clinic_members": [
                {"clinic_id": CLINIC_A, "user_id": DOCTOR, "role": "clinician"},
                {"clinic_id": CLINIC_A, "user_id": SECRETARY, "role": "delegate"},
            ],
            "patients": [
                {"id": PATIENT_A, "clinic_id": CLINIC_A},
                {"id": PATIENT_B, "clinic_id": CLINIC_B},
            ],
            "messages": [],
            "message_drafts": [],
        }
        self.rpc_calls = 0
        self.fail_decide_with: Exception | None = None

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params):
        assert name == "decide_message_draft"
        return _Rpc(self, params)

    def insert(self, name, payload):
        rows = self.tables[name]
        n = len(rows) + 1
        if name == "messages":
            row = {"id": f"msg-{n}", "read_at": None, "created_at": f"2026-01-01T00:00:{n:02d}Z"}
        else:
            row = {
                "id": f"draft-{n}", "status": "pending", "final_text": None,
                "sent_message_id": None, "decided_by": None, "decided_at": None,
                "created_at": f"2026-01-01T00:01:{n:02d}Z",
            }
            if any(r["source_message_id"] == payload["source_message_id"]
                   and r["status"] == "pending" for r in rows):
                raise RuntimeError("duplicate key: one_pending_per_message")
        row.update(payload)
        rows.append(row)
        return dict(row)

    def decide(self, p_clinic_id, p_draft_id, p_decision, p_final_text, p_decided_by):
        """Python model of decide_message_draft (migration 6)."""
        self.rpc_calls += 1
        if self.fail_decide_with:
            raise self.fail_decide_with
        draft = next((d for d in self.tables["message_drafts"]
                      if d["id"] == p_draft_id and d["clinic_id"] == p_clinic_id), None)
        if draft is None:
            raise RuntimeError("draft_not_found")
        if draft["status"] != "pending":
            raise RuntimeError("draft_not_pending")
        message = None
        if p_decision != "rejected":
            body = draft["draft_text"] if p_decision == "approved" else p_final_text
            bad_edit = not body or not body.strip() or body == draft["draft_text"]
            if p_decision == "edited" and bad_edit:
                raise RuntimeError("draft_edit_invalid")
            message = self.insert("messages", {
                "clinic_id": draft["clinic_id"], "patient_id": draft["patient_id"],
                "sender": "clinician", "body": body,
            })
            draft.update(final_text=body, sent_message_id=message["id"])
        draft.update(status=p_decision, decided_by=p_decided_by, decided_at="2026-01-02T00:00:00Z")
        return {"draft": dict(draft), "message": message}


@pytest.fixture
def audit_calls(monkeypatch):
    calls: list[dict] = []
    recorder = lambda _db, **kw: calls.append(kw)  # noqa: E731
    monkeypatch.setattr(drafts_module, "record_event", recorder)
    monkeypatch.setattr(messages_module, "record_event", recorder)
    return calls


@pytest.fixture
def model(monkeypatch):
    """Stand-in for ai.generate; records what it was asked."""
    state = {"calls": [], "text": "Thanks for letting us know. Rest and keep the area clean.",
             "error": None}

    def fake_generate(**kw):
        state["calls"].append(kw)
        if state["error"]:
            raise state["error"]
        return ai.AIResult(
            text=state["text"], model_id=MODEL, prompt_version=kw["prompt_version"],
            usage=ai.TokenUsage(input_tokens=11, output_tokens=22), stop_reason="end_turn",
        )

    monkeypatch.setattr(drafts_module.ai, "generate", fake_generate)
    return state


@pytest.fixture
def fake(monkeypatch, audit_calls, model):
    fake = _FakeSupabase()
    for module in (drafts_module, messages_module, auth_module):
        monkeypatch.setattr(module, "get_supabase", lambda: fake)
    return fake


@pytest.fixture
def client(fake):
    return TestClient(app)


def _msg(fake, mid, sender, body, patient=PATIENT_A, clinic=CLINIC_A):
    fake.tables["messages"].append({
        "id": mid, "clinic_id": clinic, "patient_id": patient, "sender": sender, "body": body,
        "read_at": None, "created_at": f"2026-01-01T00:00:{len(fake.tables['messages']):02d}Z",
    })


def _url(path="drafts", clinic=CLINIC_A, patient=PATIENT_A):
    return f"/clinics/{clinic}/patients/{patient}/{path}"


@pytest.fixture
def thread(fake):
    _msg(fake, "m1", "patient", "My incision is a bit red.")
    _msg(fake, "m2", "clinician", "How is the pain?")
    _msg(fake, "m3", "patient", "Pain is better but still red.")
    return fake


def _generate(client, **json):
    return client.post(_url(), headers=AUTH, json=json or None)


# --- generate --------------------------------------------------------------------


def test_generate_stores_a_pending_draft_and_sends_nothing(client, thread, model):
    r = _generate(client)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "pending"
    assert d["draft_text"] == model["text"]
    assert d["source_message_id"] == "m3"  # defaults to the latest patient message
    assert (d["model_id"], d["prompt_version"]) == (MODEL, "draft_reply_v1")
    assert d["final_text"] is None and d["sent_message_id"] is None
    assert d["created_by"] == DOCTOR and d["decided_by"] is None
    assert len(thread.tables["messages"]) == 3  # nothing was sent


def test_generate_prompt_has_the_thread_and_uses_clinic_and_template(client, thread, model):
    _generate(client)
    call = model["calls"][0]
    assert call["clinic_id"] == CLINIC_A and call["prompt_version"] == "draft_reply_v1"
    assert "patient: My incision is a bit red." in call["prompt"]
    assert "clinician: How is the pain?" in call["prompt"]
    assert "Pain is better but still red." in call["prompt"]
    assert "{{" not in call["system"] + call["prompt"]


def test_generate_audits_with_model_prompt_version_and_usage(client, thread, audit_calls):
    d = _generate(client).json()
    assert len(audit_calls) == 1
    call = audit_calls[0]
    assert call["event_type"] == "draft.generated" and call["clinic_id"] == CLINIC_A
    assert call["metadata"] == {
        "ref_type": "draft", "ref_id": d["id"],
        "actor": {"type": "member", "id": DOCTOR, "role": "clinician"},
        "model_id": MODEL, "prompt_version": "draft_reply_v1",
        "input_tokens": 11, "output_tokens": 22,
    }
    assert call["payload"]["draft_text"] == d["draft_text"]  # hashed by record_event
    assert d["draft_text"] not in str(call["metadata"])  # never in the readable part


def test_generate_for_a_specific_message(client, thread, model):
    d = _generate(client, message_id="m1").json()
    assert d["source_message_id"] == "m1"
    assert "My incision is a bit red." in model["calls"][0]["prompt"]


def test_generate_rejects_a_clinician_message_or_unknown_id(client, thread):
    assert _generate(client, message_id="m2").status_code == 404  # clinician-sent
    assert _generate(client, message_id="nope").status_code == 404


def test_generate_needs_a_patient_message(client, fake, model):
    _msg(fake, "c1", "clinician", "hello")
    assert _generate(client).status_code == 422
    assert model["calls"] == []


def test_generate_twice_returns_the_pending_draft_without_another_model_call(
    client, thread, model, audit_calls
):
    first = _generate(client).json()
    again = _generate(client).json()
    assert again["id"] == first["id"]
    assert len(model["calls"]) == 1 and len(audit_calls) == 1
    assert len(thread.tables["message_drafts"]) == 1


def test_a_new_draft_is_allowed_after_the_old_one_is_decided(client, thread, model):
    first = _generate(client).json()
    client.post(f"/clinics/{CLINIC_A}/drafts/{first['id']}/reject", headers=AUTH)
    second = _generate(client).json()
    assert second["id"] != first["id"] and len(model["calls"]) == 2


def test_generate_race_returns_the_winners_draft(client, thread, model, monkeypatch):
    # Our pre-check sees nothing; then the insert loses the unique index.
    real = drafts_module._pending_for
    seen = {"n": 0}

    def racy(supabase, clinic_id, source_id):
        seen["n"] += 1
        if seen["n"] == 1:
            thread.insert("message_drafts", {
                "clinic_id": CLINIC_A, "patient_id": PATIENT_A, "source_message_id": source_id,
                "draft_text": "winner", "model_id": MODEL, "prompt_version": "draft_reply_v1",
                "created_by": DOCTOR,
            })
            return None
        return real(supabase, clinic_id, source_id)

    monkeypatch.setattr(drafts_module, "_pending_for", racy)
    r = _generate(client)
    assert r.status_code == 200 and r.json()["draft_text"] == "winner"
    assert len(thread.tables["message_drafts"]) == 1


def test_daily_cap_is_a_clean_429(client, thread, model, audit_calls):
    model["error"] = ai.AIDailyCapExceeded(CLINIC_A, 200)
    r = _generate(client)
    assert r.status_code == 429 and "cap" in r.json()["detail"]
    assert thread.tables["message_drafts"] == [] and audit_calls == []


def test_model_failure_or_empty_output_is_a_502_and_stores_nothing(client, thread, model):
    model["error"] = ai.AIInvocationError("boom")
    assert _generate(client).status_code == 502
    model["error"], model["text"] = None, "   "
    assert _generate(client).status_code == 502
    assert thread.tables["message_drafts"] == []


def test_generate_reports_500_when_audit_fails(client, thread, monkeypatch):
    def boom(_db, **_kw):
        raise drafts_module.AuditWriteError("down")

    monkeypatch.setattr(drafts_module, "record_event", boom)
    r = _generate(client)
    assert r.status_code == 500 and "not audited" in r.json()["detail"]


# --- decisions -------------------------------------------------------------------


def _draft(client):
    return _generate(client).json()["id"]


def _decide(client, draft_id, action, headers=AUTH, clinic=CLINIC_A, **json):
    return client.post(f"/clinics/{clinic}/drafts/{draft_id}/{action}", headers=headers,
                       json=json or None)


def test_approve_sends_the_draft_text_as_written(client, thread, model, audit_calls):
    did = _draft(client)
    audit_calls.clear()
    r = _decide(client, did, "approve")
    assert r.status_code == 200
    body = r.json()
    assert body["draft"]["status"] == "approved"
    assert body["draft"]["final_text"] == body["draft"]["draft_text"] == model["text"]
    assert body["draft"]["decided_by"] == DOCTOR
    msg = body["message"]
    assert msg["sender"] == "clinician" and msg["body"] == model["text"]
    assert body["draft"]["sent_message_id"] == msg["id"]
    assert thread.tables["messages"][-1]["body"] == model["text"]

    assert [c["event_type"] for c in audit_calls] == ["message.sent", "draft.approved"]
    approved = audit_calls[1]
    assert approved["metadata"]["ref_type"] == "draft" and approved["metadata"]["ref_id"] == did
    assert approved["metadata"]["model_id"] == MODEL
    assert approved["metadata"]["prompt_version"] == "draft_reply_v1"
    assert approved["metadata"]["sent_message_id"] == msg["id"]
    assert approved["metadata"]["actor"] == {"type": "member", "id": DOCTOR, "role": "clinician"}
    assert audit_calls[0]["metadata"]["ref_id"] == msg["id"]


def test_edit_sends_the_edit_and_keeps_the_original(client, thread, model, audit_calls):
    did = _draft(client)
    audit_calls.clear()
    r = _decide(client, did, "edit", final_text="Please call us if the redness spreads.")
    assert r.status_code == 200
    body = r.json()
    assert body["draft"]["status"] == "edited"
    assert body["draft"]["draft_text"] == model["text"]  # original untouched
    assert body["draft"]["final_text"] == "Please call us if the redness spreads."
    assert body["message"]["body"] == "Please call us if the redness spreads."
    assert [c["event_type"] for c in audit_calls] == ["message.sent", "draft.edited"]
    payload = audit_calls[1]["payload"]
    assert payload["draft_text"] == model["text"]
    assert payload["final_text"] == "Please call us if the redness spreads."
    assert audit_calls[1]["metadata"]["prompt_version"] == "draft_reply_v1"


def test_reject_sends_nothing(client, thread, audit_calls):
    did = _draft(client)
    audit_calls.clear()
    r = _decide(client, did, "reject")
    assert r.status_code == 200
    assert r.json()["draft"]["status"] == "rejected" and r.json()["message"] is None
    assert r.json()["draft"]["decided_by"] == DOCTOR and r.json()["draft"]["final_text"] is None
    assert len(thread.tables["messages"]) == 3
    assert [c["event_type"] for c in audit_calls] == ["draft.rejected"]
    assert audit_calls[0]["metadata"]["model_id"] == MODEL


def test_each_draft_can_be_decided_only_once(client, thread):
    did = _draft(client)
    assert _decide(client, did, "approve").status_code == 200
    for action, extra in (("approve", {}), ("reject", {}), ("edit", {"final_text": "x"})):
        r = _decide(client, did, action, **extra)
        assert r.status_code == 409
    assert len(thread.tables["messages"]) == 4  # sent exactly once


def test_losing_a_race_at_the_database_is_a_409_not_a_second_send(client, thread):
    did = _draft(client)
    thread.fail_decide_with = RuntimeError("draft_not_pending")
    assert _decide(client, did, "approve").status_code == 409
    assert len(thread.tables["messages"]) == 3


def test_unexpected_database_errors_are_not_masked(client, thread):
    did = _draft(client)
    thread.fail_decide_with = RuntimeError("connection reset")
    with pytest.raises(RuntimeError, match="connection reset"):
        _decide(client, did, "approve")


def test_edit_must_actually_change_the_text(client, thread, model):
    did = _draft(client)
    r = _decide(client, did, "edit", final_text=model["text"])
    assert r.status_code == 422 and "approve" in r.json()["detail"]
    assert _decide(client, did, "edit", final_text="   ").status_code == 422
    assert _decide(client, did, "edit", final_text="").status_code == 422
    assert _decide(client, did, "edit").status_code == 422
    assert thread.rpc_calls == 0 and len(thread.tables["messages"]) == 3


def test_a_draft_is_never_sent_without_an_explicit_decision(client, thread):
    _draft(client)
    _draft(client)
    assert len(thread.tables["messages"]) == 3
    assert all(d["status"] == "pending" for d in thread.tables["message_drafts"])


def test_only_clinicians_decide_but_delegates_can_read_and_generate(client, thread):
    r = client.post(_url(), headers=SECRETARY_AUTH)
    assert r.status_code == 200
    did = r.json()["id"]
    for action, extra in (("approve", {}), ("reject", {}), ("edit", {"final_text": "x"})):
        r = _decide(client, did, action, headers=SECRETARY_AUTH, **extra)
        assert r.status_code == 403
    assert client.get(_url(), headers=SECRETARY_AUTH).status_code == 200
    assert thread.tables["message_drafts"][0]["status"] == "pending"


def test_deciding_needs_membership_and_the_right_clinic(client, thread):
    did = _draft(client)
    assert _decide(client, did, "approve", headers={}).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert _decide(client, did, "approve", headers=bad).status_code == 401
    # Member of A addressing the draft through clinic B.
    assert _decide(client, did, "approve", clinic=CLINIC_B).status_code == 403
    assert _decide(client, "no-such-draft", "approve").status_code == 404
    assert thread.tables["message_drafts"][0]["status"] == "pending"


def test_draft_from_another_clinic_is_404_even_for_a_member(client, thread):
    thread.tables["clinic_members"].append(
        {"clinic_id": CLINIC_B, "user_id": DOCTOR, "role": "clinician"}
    )
    did = _draft(client)
    # Doctor is a member of both clinics, but the draft belongs to A.
    assert _decide(client, did, "approve", clinic=CLINIC_B).status_code == 404
    assert thread.tables["message_drafts"][0]["status"] == "pending"


def test_partial_audit_failure_still_reports_500_and_tries_both_events(
    client, thread, monkeypatch, audit_calls
):
    did = _draft(client)
    audit_calls.clear()

    def flaky(_db, **kw):
        if kw["event_type"] == "message.sent":
            raise drafts_module.AuditWriteError("down")
        audit_calls.append(kw)

    monkeypatch.setattr(messages_module, "record_event", flaky)
    r = _decide(client, did, "approve")
    assert r.status_code == 500 and "not fully audited" in r.json()["detail"]
    assert [c["event_type"] for c in audit_calls] == ["draft.approved"]  # still attempted
    assert thread.tables["message_drafts"][0]["status"] == "approved"  # decision stands


# --- read ------------------------------------------------------------------------


def test_list_drafts_newest_first_with_status_filter(client, thread):
    first = _draft(client)
    _decide(client, first, "reject")
    second = _draft(client)
    listed = client.get(_url(), headers=AUTH).json()
    assert [d["id"] for d in listed] == [second, first]
    pending = client.get(_url() + "?status=pending", headers=AUTH).json()
    assert [d["id"] for d in pending] == [second]
    assert client.get(_url() + "?status=bogus", headers=AUTH).status_code == 422


def test_list_is_scoped_to_the_clinic_and_patient(client, thread):
    _draft(client)
    assert client.get(_url(clinic=CLINIC_B, patient=PATIENT_B), headers=AUTH).status_code == 403
    assert client.get(_url(patient=PATIENT_B), headers=AUTH).status_code == 404
    assert client.get(_url()).status_code == 401


def test_there_is_no_way_to_delete_a_draft(client, thread):
    did = _draft(client)
    r = client.delete(f"/clinics/{CLINIC_A}/drafts/{did}", headers=AUTH)
    assert r.status_code == 405 or r.status_code == 404
    assert len(thread.tables["message_drafts"]) == 1
