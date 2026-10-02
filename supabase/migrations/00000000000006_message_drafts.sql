-- AI-drafted message replies (feature: draft -> clinician decision -> send).
--
-- A draft is a model-written reply to one patient message. It is NEVER sent by
-- itself: it sits `pending` until a clinician explicitly approves it as written,
-- edits it and sends the edit, or rejects it. This table is the record of that:
-- exactly what the model wrote (draft_text), which model and prompt version wrote
-- it, what was actually sent (final_text -> sent_message_id) and who decided.
--
-- Drafts are retained permanently:
--   * the original columns can never change after insert,
--   * a decided draft can never change again,
--   * rows can never be deleted (even by the service role), and the foreign keys
--     deliberately have no ON DELETE CASCADE, so deleting a clinic/patient/message
--     that a draft points at is refused rather than silently erasing the record.
-- If a retention policy is ever decided, relaxing this is a deliberate migration.
--
-- Reads: clinic members (RLS). Writes: apps/api only (service role), which checks
-- clinic ownership and the clinician's role, and audits every transition
-- (draft.generated / .approved / .edited / .rejected).

create table message_drafts (
  id                 uuid primary key default gen_random_uuid(),
  clinic_id          uuid not null references clinics (id),
  patient_id         uuid not null references patients (id),
  -- The patient message this draft replies to. (There is no separate thread
  -- table: a thread is a patient's messages within a clinic.)
  source_message_id  uuid not null references messages (id),
  draft_text         text not null check (char_length(btrim(draft_text)) > 0),
  model_id           text not null,
  prompt_version     text not null,
  status             text not null default 'pending'
                       check (status in ('pending', 'approved', 'edited', 'rejected')),
  final_text         text,                              -- what was sent; null until approved/edited
  sent_message_id    uuid references messages (id),     -- the message that was sent
  created_by         uuid not null references auth.users (id),
  created_at         timestamptz not null default now(),
  decided_by         uuid references auth.users (id),   -- the approving / rejecting clinician
  decided_at         timestamptz,

  constraint message_drafts_state_consistent check (
    (status = 'pending'
       and final_text is null and sent_message_id is null
       and decided_by is null and decided_at is null)
    or (status = 'approved'
       and final_text = draft_text and sent_message_id is not null
       and decided_by is not null and decided_at is not null)
    or (status = 'edited'
       and final_text is not null and char_length(btrim(final_text)) > 0
       and final_text <> draft_text and sent_message_id is not null
       and decided_by is not null and decided_at is not null)
    or (status = 'rejected'
       and final_text is null and sent_message_id is null
       and decided_by is not null and decided_at is not null)
  )
);

-- One open draft per patient message: asking again returns the pending one
-- instead of paying for a second model call.
create unique index message_drafts_one_pending_per_message
  on message_drafts (source_message_id) where status = 'pending';

-- A sent message came from at most one draft.
create unique index message_drafts_sent_message_unique
  on message_drafts (sent_message_id) where sent_message_id is not null;

create index message_drafts_patient_idx on message_drafts (clinic_id, patient_id, created_at desc);

alter table message_drafts enable row level security;

create policy "clinic members can read their clinic's drafts" on message_drafts
  for select using (is_clinic_member(clinic_id));

-- No write policies, and the privileges go too: only the audited api path writes.
revoke insert, update, delete on message_drafts from authenticated, anon;

-- Permanence, enforced by the database rather than by convention.
create or replace function message_drafts_guard() returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'message_drafts_retained: drafts are never deleted';
  end if;
  if old.status <> 'pending' then
    raise exception 'message_drafts_decided: a decided draft cannot change';
  end if;
  if (new.id, new.clinic_id, new.patient_id, new.source_message_id, new.draft_text,
      new.model_id, new.prompt_version, new.created_by, new.created_at)
     is distinct from
     (old.id, old.clinic_id, old.patient_id, old.source_message_id, old.draft_text,
      old.model_id, old.prompt_version, old.created_by, old.created_at) then
    raise exception 'message_drafts_original: the original draft cannot be altered';
  end if;
  return new;
end;
$$;

create trigger message_drafts_guard
  before update or delete on message_drafts
  for each row execute function message_drafts_guard();

-- The clinician's decision, atomically: lock the draft, require it to still be
-- pending, send the message (approved / edited), and record the outcome. One
-- transaction means two clinicians can't both send the same draft and a crash
-- can't leave a draft marked sent with no message. Audit events are written by
-- the api afterwards, like every other write.
--   p_decision: 'approved' (sends draft_text as written), 'edited' (sends
--   p_final_text, which must differ from the draft), or 'rejected' (sends nothing).
create or replace function decide_message_draft(
  p_clinic_id  uuid,
  p_draft_id   uuid,
  p_decision   text,
  p_final_text text,
  p_decided_by uuid
) returns jsonb
language plpgsql
as $$
declare
  d message_drafts;
  m messages;
  body text;
begin
  if p_decision not in ('approved', 'edited', 'rejected') then
    raise exception 'draft_bad_decision';
  end if;

  select * into d from message_drafts
  where id = p_draft_id and clinic_id = p_clinic_id
  for update;
  if not found then
    raise exception 'draft_not_found';
  end if;
  if d.status <> 'pending' then
    raise exception 'draft_not_pending';
  end if;

  if p_decision = 'rejected' then
    update message_drafts
       set status = 'rejected', decided_by = p_decided_by, decided_at = now()
     where id = d.id
    returning * into d;
    return jsonb_build_object('draft', to_jsonb(d), 'message', null);
  end if;

  if p_decision = 'approved' then
    body := d.draft_text;
  else
    if p_final_text is null or char_length(btrim(p_final_text)) = 0
       or p_final_text = d.draft_text then
      raise exception 'draft_edit_invalid';
    end if;
    body := p_final_text;
  end if;

  insert into messages (clinic_id, patient_id, sender, body)
  values (d.clinic_id, d.patient_id, 'clinician', body)
  returning * into m;

  update message_drafts
     set status = p_decision, final_text = body, sent_message_id = m.id,
         decided_by = p_decided_by, decided_at = now()
   where id = d.id
  returning * into d;

  return jsonb_build_object('draft', to_jsonb(d), 'message', to_jsonb(m));
end;
$$;

-- Only the service role (apps/api) may decide.
revoke execute on function decide_message_draft from public, anon, authenticated;
grant execute on function decide_message_draft to service_role;
