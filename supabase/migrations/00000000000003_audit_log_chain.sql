-- Audit log: hash chain + signature columns, and the atomic append function.
--
-- The api computes each row's hash and Ed25519 signature (core/audit.py); the
-- database's job is to make appending race-free and to refuse forks:
--   * append_audit_event() takes a per-clinic advisory lock, checks that the
--     caller's prev_hash is still the clinic's chain head, and only then inserts.
--     A stale prev_hash raises 'audit_chain_conflict' and the api retries.
--   * a unique index on (clinic_id, prev_hash) means the chain can't fork even
--     if something bypasses the function.
-- audit_log stays append-only: UPDATE/DELETE are already revoked in migration 1.

alter table audit_log
  add column row_hash  text not null,   -- sha256 of the canonical signed fields; next row's prev_hash
  add column signature text not null,   -- Ed25519 over row_hash, base64
  add column key_id    text not null;   -- which signing key; lets keys rotate without orphaning old rows

create unique index audit_log_chain_no_fork
  on audit_log (clinic_id, coalesce(prev_hash, ''));

create or replace function append_audit_event(
  p_clinic_id    uuid,
  p_event_type   text,
  p_payload_hash text,
  p_prev_hash    text,
  p_row_hash     text,
  p_signature    text,
  p_key_id       text,
  p_metadata     jsonb,
  p_created_at   timestamptz
) returns audit_log
language plpgsql
as $$
declare
  head text;
  inserted audit_log;
begin
  perform pg_advisory_xact_lock(hashtextextended(p_clinic_id::text, 0));

  select row_hash into head
  from audit_log
  where clinic_id = p_clinic_id
  order by id desc
  limit 1;

  if head is distinct from p_prev_hash then
    raise exception 'audit_chain_conflict';
  end if;

  insert into audit_log
    (clinic_id, event_type, payload_hash, prev_hash, row_hash, signature, key_id, metadata, created_at)
  values
    (p_clinic_id, p_event_type, p_payload_hash, p_prev_hash, p_row_hash, p_signature, p_key_id,
     p_metadata, p_created_at)
  returning * into inserted;

  return inserted;
end;
$$;

-- Only the service role (apps/api) may append.
revoke execute on function append_audit_event from public, anon, authenticated;
grant execute on function append_audit_event to service_role;
