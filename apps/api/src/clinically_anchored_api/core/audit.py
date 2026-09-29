"""Hash-chained, Ed25519-signed audit log writer (data-catalogue D16/D17).

Every audited event becomes one `audit_log` row:

  payload_hash  salted sha256 of the event payload -- the row holds no raw
                content, only a commitment someone holding the payload can check
  prev_hash     row_hash of the clinic's previous row (null for the first)
  row_hash      sha256 of the canonical signed fields, incl. prev_hash
  signature     Ed25519 over row_hash, verifiable with the public key alone

Editing or deleting a row breaks every later row_hash; re-forging the chain
needs the private key. (Someone holding the key AND database write access
could rewrite history -- external anchoring of the chain head, planned for v2,
is the defence against that.)

`metadata` is readable by clinic members via RLS, so it carries only ids and
the salt -- never message text or answers.

The database serialises appends per clinic (see migration 3); this module
retries when another writer moved the chain head first.
"""

import base64
import hashlib
import json
import random
import secrets
import time
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from clinically_anchored_api.core.config import get_settings

_MAX_ATTEMPTS = 15
_PAGE = 1000


class AuditWriteError(Exception):
    """The audit event could not be recorded."""


def canonical_json(value: Any) -> bytes:
    """Deterministic serialisation: sorted keys, no whitespace, ASCII only."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def format_ts(ts: datetime | str) -> str:
    """Fixed-format UTC timestamp, so signing and verification agree no matter
    how the database renders timestamptz on the way back."""
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def payload_hash(payload: dict, salt: str) -> str:
    return hashlib.sha256(salt.encode() + b"\x00" + canonical_json(payload)).hexdigest()


def compute_row_hash(
    *,
    clinic_id: str,
    event_type: str,
    payload_hash: str,
    prev_hash: str | None,
    metadata: dict,
    created_at: str,
) -> str:
    signed = {
        "clinic_id": clinic_id,
        "event_type": event_type,
        "payload_hash": payload_hash,
        "prev_hash": prev_hash,
        "metadata": metadata,
        "created_at": created_at,
    }
    return hashlib.sha256(canonical_json(signed)).hexdigest()


def _private_key() -> Ed25519PrivateKey:
    raw = get_settings().audit_signing_key
    if not raw:
        raise AuditWriteError(
            "AUDIT_SIGNING_KEY is not set. Generate one with "
            "`python -m clinically_anchored_api.core.audit` and add it to .env."
        )
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw))


def public_key_b64() -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw = _private_key().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def trusted_public_keys() -> dict[str, Ed25519PublicKey]:
    """key_id -> public key for verification: the configured extras plus the
    current signing key (which wins if the ids collide)."""
    settings = get_settings()
    keys: dict[str, Ed25519PublicKey] = {}
    if settings.audit_public_keys:
        try:
            extra = json.loads(settings.audit_public_keys)
            for key_id, b64 in extra.items():
                keys[key_id] = Ed25519PublicKey.from_public_bytes(base64.b64decode(b64))
        except (ValueError, AttributeError, TypeError) as exc:
            raise AuditWriteError(f"AUDIT_PUBLIC_KEYS is malformed: {exc}") from exc
    keys[settings.audit_key_id] = _private_key().public_key()
    return keys


def record_event(
    supabase,
    *,
    clinic_id: str,
    event_type: str,
    payload: dict,
    metadata: dict,
) -> dict:
    """Append one event to the clinic's chain. `payload` is hashed (never
    stored); `metadata` is stored in the clear, so ids only."""
    settings = get_settings()
    key = _private_key()

    salt = secrets.token_hex(16)
    stored_metadata = {**metadata, "salt": salt}
    p_hash = payload_hash(payload, salt)

    for attempt in range(_MAX_ATTEMPTS):
        if attempt:
            # Jittered backoff: without it, writers that collide once retry in
            # lockstep and keep colliding (seen with 8 parallel writers).
            time.sleep(random.uniform(0, 0.03 * attempt))
        head = (
            supabase.table("audit_log")
            .select("row_hash")
            .eq("clinic_id", clinic_id)
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        prev_hash = head.data[0]["row_hash"] if head.data else None
        created_at = format_ts(datetime.now(UTC))
        row_hash = compute_row_hash(
            clinic_id=clinic_id,
            event_type=event_type,
            payload_hash=p_hash,
            prev_hash=prev_hash,
            metadata=stored_metadata,
            created_at=created_at,
        )
        signature = base64.b64encode(key.sign(bytes.fromhex(row_hash))).decode()
        try:
            result = supabase.rpc(
                "append_audit_event",
                {
                    "p_clinic_id": clinic_id,
                    "p_event_type": event_type,
                    "p_payload_hash": p_hash,
                    "p_prev_hash": prev_hash,
                    "p_row_hash": row_hash,
                    "p_signature": signature,
                    "p_key_id": settings.audit_key_id,
                    "p_metadata": stored_metadata,
                    "p_created_at": created_at,
                },
            ).execute()
        except Exception as exc:
            if "audit_chain_conflict" in str(exc):
                continue  # another writer moved the head; re-read and re-sign
            raise AuditWriteError(f"audit append failed: {exc}") from exc
        return result.data

    raise AuditWriteError("audit append kept conflicting; giving up")


def verify_chain(rows: list[dict], keys: dict[str, Ed25519PublicKey]) -> dict:
    """Check rows (one clinic, oldest first), verifying each signature with the
    public key named by the row's key_id. Returns
    {"ok": bool, "count": n, "head_hash": ..., "broken_at": row id or None,
    "reason": ...}."""
    prev: str | None = None
    for row in rows:
        expected = compute_row_hash(
            clinic_id=row["clinic_id"],
            event_type=row["event_type"],
            payload_hash=row["payload_hash"],
            prev_hash=row["prev_hash"],
            metadata=row["metadata"],
            created_at=format_ts(row["created_at"]),
        )
        if row["prev_hash"] != prev:
            return _broken(rows, row, "chain link does not match previous row")
        if row["row_hash"] != expected:
            return _broken(rows, row, "row contents do not match row_hash")
        public_key = keys.get(row["key_id"])
        if public_key is None:
            return _broken(rows, row, f"unknown signing key {row['key_id']!r}")
        try:
            public_key.verify(base64.b64decode(row["signature"]), bytes.fromhex(expected))
        except Exception:
            return _broken(rows, row, "signature invalid")
        prev = row["row_hash"]
    return {
        "ok": True,
        "count": len(rows),
        "head_hash": prev,
        "broken_at": None,
        "reason": None,
        "key_ids": sorted({r["key_id"] for r in rows}),
    }


def _broken(rows: list[dict], row: dict, reason: str) -> dict:
    return {
        "ok": False,
        "count": len(rows),
        "head_hash": None,
        "broken_at": row["id"],
        "reason": reason,
        "key_ids": sorted({r["key_id"] for r in rows}),
    }


def fetch_chain(supabase, clinic_id: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        page = (
            supabase.table("audit_log")
            .select("*")
            .eq("clinic_id", clinic_id)
            .order("id")
            .range(offset, offset + _PAGE - 1)
            .execute()
        )
        rows.extend(page.data)
        if len(page.data) < _PAGE:
            return rows
        offset += _PAGE


if __name__ == "__main__":
    # Key generation helper: prints an AUDIT_SIGNING_KEY value.
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    seed = Ed25519PrivateKey.generate().private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    print(base64.b64encode(seed).decode())
