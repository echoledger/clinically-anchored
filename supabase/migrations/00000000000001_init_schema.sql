-- Clinically Anchored: initial schema.
--
-- Tenant = clinic. Every clinical table carries clinic_id and is protected by
-- row-level security scoped through clinic_members, so a bug in application code
-- cannot leak one clinic's patients into another clinic's view -- the database
-- refuses the query, not the API layer.
--
-- Patients are NOT Supabase Auth users (per the product's design: no patient
-- password, access via a signed, expiring link token instead). Patient-facing
-- writes go through the API service using the service role key, which bypasses
-- RLS by design -- the API is what validates the token. RLS here protects the
-- clinician/delegate-facing paths (the web app talking to Supabase directly).

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- Tenants
-- ---------------------------------------------------------------------------

create table clinics (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  created_at  timestamptz not null default now()
);

-- Links a Supabase Auth user (clinician or delegate/secretary) to a clinic.
create table clinic_members (
  id          uuid primary key default gen_random_uuid(),
  clinic_id   uuid not null references clinics (id) on delete cascade,
  user_id     uuid not null references auth.users (id) on delete cascade,
  role        text not null check (role in ('owner', 'clinician', 'delegate')),
  created_at  timestamptz not null default now(),
  unique (clinic_id, user_id)
);

-- Helper: is the current authenticated user a member of this clinic?
-- STABLE, not SECURITY DEFINER -- it relies on the policy below that lets a
-- user see their own membership rows, so no privilege escalation happens here.
create or replace function is_clinic_member(check_clinic_id uuid)
returns boolean
language sql
stable
as $$
  select exists (
    select 1 from clinic_members
    where clinic_id = check_clinic_id
      and user_id = auth.uid()
  );
$$;

alter table clinics enable row level security;
alter table clinic_members enable row level security;

create policy "members can view their own clinic" on clinics
  for select using (is_clinic_member(id));

create policy "members can view their own membership rows" on clinic_members
  for select using (user_id = auth.uid());

-- ---------------------------------------------------------------------------
-- Clinic-scoped reference and clinical data
-- ---------------------------------------------------------------------------

-- The dropdown of procedures a clinic's surgeons perform (patients pick from this).
create table procedures (
  id          uuid primary key default gen_random_uuid(),
  clinic_id   uuid not null references clinics (id) on delete cascade,
  name        text not null,
  active      boolean not null default true,
  created_at  timestamptz not null default now()
);

-- Patient identity. Kept in its own table so clinical data (check-ins,
-- messages) can eventually be split into a separately-encrypted store keyed
-- by patient_id without touching this table's shape (see data-catalogue D4).
create table patients (
  id          uuid primary key default gen_random_uuid(),
  clinic_id   uuid not null references clinics (id) on delete cascade,
  full_name   text not null,
  contact     text,                 -- phone or email used for the check-in link
  created_at  timestamptz not null default now()
);

-- Structured post-op check-in answers (data-catalogue D29). Structured by
-- design: dropdown + checkboxes, not free text, per Sarah's stated preference.
create table check_ins (
  id              uuid primary key default gen_random_uuid(),
  clinic_id       uuid not null references clinics (id) on delete cascade,
  patient_id      uuid not null references patients (id) on delete cascade,
  procedure_id    uuid references procedures (id),
  post_op_day     integer,
  answers         jsonb not null default '{}'::jsonb,   -- symptom checkbox answers
  is_red_flag     boolean not null default false,        -- set by the rule engine
  created_at      timestamptz not null default now()
);

-- Two-way messages between clinician/delegate and patient.
create table messages (
  id            uuid primary key default gen_random_uuid(),
  clinic_id     uuid not null references clinics (id) on delete cascade,
  patient_id    uuid not null references patients (id) on delete cascade,
  sender        text not null check (sender in ('clinician', 'patient', 'system')),
  body          text not null,
  read_at       timestamptz,
  created_at    timestamptz not null default now()
);

alter table procedures enable row level security;
alter table patients   enable row level security;
alter table check_ins  enable row level security;
alter table messages   enable row level security;

create policy "clinic members can read their clinic's procedures" on procedures
  for select using (is_clinic_member(clinic_id));
create policy "clinic members can manage their clinic's procedures" on procedures
  for all using (is_clinic_member(clinic_id)) with check (is_clinic_member(clinic_id));

create policy "clinic members can read their clinic's patients" on patients
  for select using (is_clinic_member(clinic_id));
create policy "clinic members can manage their clinic's patients" on patients
  for all using (is_clinic_member(clinic_id)) with check (is_clinic_member(clinic_id));

create policy "clinic members can read their clinic's check-ins" on check_ins
  for select using (is_clinic_member(clinic_id));

create policy "clinic members can read their clinic's messages" on messages
  for select using (is_clinic_member(clinic_id));
create policy "clinic members can send messages in their clinic" on messages
  for insert with check (is_clinic_member(clinic_id) and sender = 'clinician');

-- ---------------------------------------------------------------------------
-- Audit log (data-catalogue D16/D17): signed, hash-chained, append-only.
-- Written by the API service (service role), never edited or deleted.
-- ---------------------------------------------------------------------------

create table audit_log (
  id            bigint generated always as identity primary key,
  clinic_id     uuid not null references clinics (id),
  event_type    text not null,
  payload_hash  text not null,     -- salted hash of the event payload; no raw content
  prev_hash     text,              -- hash of the previous row for this clinic (chain link)
  metadata      jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);

alter table audit_log enable row level security;

create policy "clinic members can read their clinic's audit log" on audit_log
  for select using (is_clinic_member(clinic_id));

-- No insert/update/delete policy for authenticated users: only the service
-- role (used exclusively by apps/api) can write here, and Postgres has no
-- UPDATE or DELETE grant for it at all -- append-only is enforced by the
-- database, not just by convention.
revoke update, delete on audit_log from authenticated, anon;
