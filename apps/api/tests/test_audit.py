"""Audit log writer: chain linking, signing, conflict retry, tamper
detection, and the verify endpoint -- against an in-memory fake that mimics
append_audit_event's head check."""

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from fastapi.testclient import TestClient

from clinically_anchored_api.api import audit as audit_api
from clinically_anchored_api.core import audit
from clinically_anchored_api.core import auth as auth_module
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.main import app

CLINIC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
USER = "99999999-9999-9999-9999-999999999999"


class _Q:
    def __init__(self, db):
        self.db, self.f, self.desc, self.lim, self.rng = db, [], False, None, None

    def select(self, *_a):
        return self

    def eq(self, c, v):
        self.f.append((c, v))
        return self

    def order(self, _c, desc=False):
        self.desc = desc
        return self

    def limit(self, n):
        self.lim = n
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def execute(self):
        rows = [r for r in self.db.rows if all(r.get(c) == v for c, v in self.f)]
        rows.sort(key=lambda r: r["id"], reverse=self.desc)
        if self.rng:
            rows = rows[self.rng[0] : self.rng[1] + 1]
        if self.lim:
            rows = rows[: self.lim]
        return type("R", (), {"data": rows})()


class _Members:
    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def execute(self):
        return type("R", (), {"data": [{"role": "owner"}]})()


class _Rpc:
    def __init__(self, db, params):
        self.db, self.params = db, params

    def execute(self):
        return self.db.append(self.params)


class _FakeDb:
    def __init__(self):
        self.rows: list[dict] = []
        self.conflicts_to_inject = 0
        self.auth = type("A", (), {"get_user": lambda _s, _j: type(
            "U", (), {"user": type("X", (), {"id": USER})()})()})()

    def table(self, name):
        if name == "clinic_members":
            return _Members()
        assert name == "audit_log"
        return _Q(self)

    def rpc(self, name, params):
        assert name == "append_audit_event"
        return _Rpc(self, params)

    def append(self, p):
        if self.conflicts_to_inject:
            self.conflicts_to_inject -= 1
            raise RuntimeError("audit_chain_conflict")
        mine = [r for r in self.rows if r["clinic_id"] == p["p_clinic_id"]]
        head = mine[-1]["row_hash"] if mine else None
        if head != p["p_prev_hash"]:
            raise RuntimeError("audit_chain_conflict")
        row = {
            "id": len(self.rows) + 1,
            "clinic_id": p["p_clinic_id"],
            "event_type": p["p_event_type"],
            "payload_hash": p["p_payload_hash"],
            "prev_hash": p["p_prev_hash"],
            "row_hash": p["p_row_hash"],
            "signature": p["p_signature"],
            "key_id": p["p_key_id"],
            "metadata": p["p_metadata"],
            "created_at": p["p_created_at"],
        }
        self.rows.append(row)
        return type("R", (), {"data": row})()


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    seed = Ed25519PrivateKey.generate().private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    monkeypatch.setattr(get_settings(), "audit_signing_key", base64.b64encode(seed).decode())


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(audit.time, "sleep", lambda _s: None)


@pytest.fixture
def db():
    return _FakeDb()


def _pub():
    """key_id -> public key, as the verify endpoint builds it."""
    return audit.trusted_public_keys()


def _record(db, clinic=CLINIC, body="hello"):
    return audit.record_event(
        db,
        clinic_id=clinic,
        event_type="message.sent",
        payload={"body": body},
        metadata={"ref_type": "message", "ref_id": "m1"},
    )


def test_rows_chain_and_verify(db):
    first, second, third = _record(db), _record(db), _record(db)
    assert first["prev_hash"] is None
    assert second["prev_hash"] == first["row_hash"]
    assert third["prev_hash"] == second["row_hash"]
    result = audit.verify_chain(db.rows, _pub())
    assert result["ok"] and result["count"] == 3 and result["head_hash"] == third["row_hash"]


def test_chains_are_per_clinic(db):
    _record(db, CLINIC)
    other = _record(db, OTHER)
    assert other["prev_hash"] is None


def test_payload_is_not_stored(db):
    _record(db, body="very private text")
    assert "very private text" not in str(db.rows)


def test_same_payload_gets_different_hash_each_time(db):
    a, b = _record(db), _record(db)
    assert a["payload_hash"] != b["payload_hash"]  # per-event salt


def test_retries_on_chain_conflict(db):
    db.conflicts_to_inject = 2
    _record(db)
    assert len(db.rows) == 1


def test_gives_up_after_repeated_conflicts(db):
    db.conflicts_to_inject = 99
    with pytest.raises(audit.AuditWriteError):
        _record(db)


def test_missing_key_is_a_clear_error(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "audit_signing_key", "")
    with pytest.raises(audit.AuditWriteError, match="AUDIT_SIGNING_KEY"):
        _record(db)


@pytest.mark.parametrize(
    "tamper",
    [
        lambda rows: rows[1].update(event_type="message.deleted"),
        lambda rows: rows[1].update(metadata={**rows[1]["metadata"], "ref_id": "other"}),
        lambda rows: rows[1].update(payload_hash="0" * 64),
        lambda rows: rows[1].update(created_at="2020-01-01T00:00:00.000000Z"),
        lambda rows: rows.pop(1),  # deleted row
    ],
)
def test_verify_detects_tampering(db, tamper):
    for _ in range(3):
        _record(db)
    tamper(db.rows)
    assert audit.verify_chain(db.rows, _pub())["ok"] is False


def test_verify_detects_reforged_row_signed_by_other_key(db):
    for _ in range(2):
        _record(db)
    # Attacker recomputes hash for a modified row but signs with their own key.
    row = db.rows[1]
    row["event_type"] = "message.other"
    row["row_hash"] = audit.compute_row_hash(
        clinic_id=row["clinic_id"], event_type=row["event_type"],
        payload_hash=row["payload_hash"], prev_hash=row["prev_hash"],
        metadata=row["metadata"], created_at=audit.format_ts(row["created_at"]),
    )
    row["signature"] = base64.b64encode(
        Ed25519PrivateKey.generate().sign(bytes.fromhex(row["row_hash"]))
    ).decode()
    result = audit.verify_chain(db.rows, _pub())
    assert not result["ok"] and result["reason"] == "signature invalid"


def test_verify_accepts_db_style_timestamps(db):
    _record(db)
    db.rows[0]["created_at"] = db.rows[0]["created_at"].replace("Z", "+00:00")
    assert audit.verify_chain(db.rows, _pub())["ok"]


def test_verify_endpoint(db, monkeypatch):
    monkeypatch.setattr(audit_api, "get_supabase", lambda: db)
    monkeypatch.setattr(auth_module, "get_supabase", lambda: db)
    _record(db)
    _record(db)
    r = TestClient(app).get(
        f"/clinics/{CLINIC}/audit-log/verify", headers={"Authorization": "Bearer x"}
    )
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["count"] == 2
    assert body["public_key"] == audit.public_key_b64()


def _switch_key(monkeypatch, key_id):
    """Simulate rotating (or a different environment signing): new key, new id."""
    seed = Ed25519PrivateKey.generate().private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "audit_signing_key", base64.b64encode(seed).decode())
    monkeypatch.setattr(settings, "audit_key_id", key_id)
    return audit.public_key_b64()


def test_chain_signed_by_two_keys_verifies_when_old_key_is_configured(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "audit_key_id", "dev-1")
    _record(db)
    _record(db)
    old_pub = audit.public_key_b64()
    _switch_key(monkeypatch, "prod-1")
    _record(db)

    # Without the old key configured, the old rows can't be checked.
    broken = audit.verify_chain(db.rows, audit.trusted_public_keys())
    assert not broken["ok"] and broken["broken_at"] == 1
    assert "unknown signing key 'dev-1'" in broken["reason"]

    monkeypatch.setattr(get_settings(), "audit_public_keys", json.dumps({"dev-1": old_pub}))
    result = audit.verify_chain(db.rows, audit.trusted_public_keys())
    assert result["ok"] and result["count"] == 3
    assert result["key_ids"] == ["dev-1", "prod-1"]


def test_row_relabelled_with_wrong_key_id_fails(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "audit_key_id", "dev-1")
    _record(db)
    old_pub = audit.public_key_b64()
    _switch_key(monkeypatch, "prod-1")
    _record(db)
    monkeypatch.setattr(get_settings(), "audit_public_keys", json.dumps({"dev-1": old_pub}))
    db.rows[1]["key_id"] = "dev-1"  # claim the prod-signed row came from the old key
    result = audit.verify_chain(db.rows, audit.trusted_public_keys())
    assert not result["ok"] and result["reason"] == "signature invalid"


def test_malformed_public_keys_config_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(get_settings(), "audit_public_keys", "not json")
    with pytest.raises(audit.AuditWriteError, match="AUDIT_PUBLIC_KEYS"):
        audit.trusted_public_keys()
    monkeypatch.setattr(get_settings(), "audit_public_keys", '{"dev-1": "!!notbase64!!"}')
    with pytest.raises(audit.AuditWriteError, match="AUDIT_PUBLIC_KEYS"):
        audit.trusted_public_keys()
