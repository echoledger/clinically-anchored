-- Consent records (data-catalogue D12).
--
-- Deliberately generic: what consent_type values exist, what the wording says
-- and what each one gates (if anything) are product/legal decisions that are
-- still open, so nothing about them is encoded here. A row records "this
-- patient agreed to <consent_type>, as worded in <consent_text_version>, at
-- <granted_at>" and, if they later withdraw, <revoked_at>. The wording itself
-- lives in the web app / a future template library, referenced by version.
--
-- One row per grant; a withdrawal sets revoked_at on the row (history is kept,
-- a later re-grant is a new row). Written only by apps/api (service role),
-- which validates the patient's link token and audits every grant/revoke
-- (consent.granted / consent.revoked). Clinic members can read; nobody
-- authenticated can write.

create table consents (
  id                    uuid primary key default gen_random_uuid(),
  clinic_id             uuid not null references clinics (id) on delete cascade,
  patient_id            uuid not null references patients (id) on delete cascade,
  consent_type          text not null check (consent_type ~ '^[a-z][a-z0-9_]{0,63}$'),
  consent_text_version  text not null check (char_length(consent_text_version) between 1 and 64),
  granted_at            timestamptz not null default now(),
  revoked_at            timestamptz
);

-- At most one live grant per patient / type / wording version, so a retried
-- or double-submitted grant can't create duplicates.
create unique index consents_one_active_grant
  on consents (patient_id, consent_type, consent_text_version)
  where revoked_at is null;

-- "What has this patient consented to?" reads.
create index consents_patient_idx on consents (clinic_id, patient_id, granted_at desc);

alter table consents enable row level security;

create policy "clinic members can read their clinic's consents" on consents
  for select using (is_clinic_member(clinic_id));

-- No insert/update/delete policy, and the table privileges go too: a consent
-- record is evidence, so the only writer is the audited api path.
revoke insert, update, delete on consents from authenticated, anon;
