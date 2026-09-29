-- Clinician review state for check-ins, plus indexes for the queue.
--
-- The queue shows unreviewed check-ins (red flags first) and unread patient
-- messages. A clinician marks a check-in reviewed via apps/api, which also
-- writes a 'check_in.reviewed' audit event. Nullable + additive: existing rows
-- simply count as unreviewed.

alter table check_ins
  add column reviewed_at timestamptz,
  add column reviewed_by uuid references auth.users (id);

-- Queue reads: "unreviewed check-ins for this clinic, newest first".
create index check_ins_unreviewed_idx
  on check_ins (clinic_id, created_at desc)
  where reviewed_at is null;

-- Queue reads: "unread patient messages for this clinic".
create index messages_unread_patient_idx
  on messages (clinic_id, patient_id, created_at)
  where sender = 'patient' and read_at is null;
