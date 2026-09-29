-- Synthetic dev/demo data. Fixed UUIDs so this migration is idempotent and
-- re-runnable (ON CONFLICT DO NOTHING). Invented clinic and patient only --
-- see data-catalogue.md: no real patient goes anywhere near this project
-- until the privacy review is done.

insert into clinics (id, name)
values ('00000000-0000-0000-0000-0000000000c1', 'Major Mackenzie Surgery (dev)')
on conflict (id) do nothing;

insert into procedures (id, clinic_id, name)
values
  ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000c1', 'Laparoscopic colectomy'),
  ('00000000-0000-0000-0000-0000000000d2', '00000000-0000-0000-0000-0000000000c1', 'Hemorrhoidectomy'),
  ('00000000-0000-0000-0000-0000000000d3', '00000000-0000-0000-0000-0000000000c1', 'Ileostomy reversal')
on conflict (id) do nothing;

insert into patients (id, clinic_id, full_name, contact)
values ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000c1', 'Test Patient', 'test-patient@example.com')
on conflict (id) do nothing;
