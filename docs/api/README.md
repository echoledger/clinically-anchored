# api — service brief

Read this before touching `apps/api`. It's the product context a fresh thread needs;
`apps/api/README.md` has the dev-loop commands.

## What this service owns

`apps/api` (FastAPI, Python, deployed to Railway) is the only thing that talks to
Postgres directly and the only thing that calls out to an LLM. Concretely, it owns:

- **Check-in intake.** Patients submit structured answers (procedure dropdown +
  symptom checkboxes) via a signed link, no login. This service validates the link
  token, writes the check-in, and runs it through the red-flag rules.
- **The red-flag rule engine.** Fixed rules a clinician writes, not model judgement —
  nothing decides on its own that a patient is fine. A check-in or message that
  matches a rule gets pulled to the top of the clinician's queue.
- **Rolling summary generation.** Rewritten each time something new arrives in a
  thread, so it's never stale. Every generated line links back to the message it
  came from — this is a hard product requirement, not a nice-to-have (see "What you
  would see" in the patient-facing Overview doc: the clinician has to be able to
  check any claim in one tap).
- **The hash-chained audit log writer** (`audit_log` table — see docs/database).
  Every message, check-in, consent, and AI-draft-approval event gets a signed record
  chained to the previous one for that clinic. This is the MVP's actual differentiator,
  not the optional blockchain layer — the product works fully with no chain involved.
  Public anchoring via AnchorRegistry is v2, optional, and a separate downstream
  batcher; don't build toward it yet.

`apps/web` never does any of the above. It only reads/writes through this service's
HTTP API (OpenAPI schema at `/openapi.json`), and it authenticates end users through
Supabase Auth directly for clinicians — this service uses the Supabase *service role*
key, which bypasses row-level security by design. That means this service is what's
responsible for authorization logic that RLS can't express (e.g. validating a
patient's link token), not the database.

## Current state

Built (branch `feature/check-in-intake`, not yet merged):

- Check-in intake: `POST /check-ins`, `GET /check-ins/context`, signed/expiring link
  tokens (`core/security.py`), dev-only `POST /dev/check-in-links`.
- Red-flag rules (`core/rules.py`) -- **placeholder logic**, not Sarah's real list.
- Clinician auth (`core/auth.py`): Supabase JWT + `clinic_members` check per clinic.
- Messages: clinician side (list thread, send, mark read; JWT auth) and patient side
  (`GET`/`POST /messages?token=`; link-token auth, sender forced to `patient`). Both send
  paths share one insert-and-audit function. Note: the patient link token also grants
  read access to the whole thread, and there is no rate limiting yet.
- Audit log writer (`core/audit.py`): salted payload hash, per-clinic hash chain,
  Ed25519 signature, atomic append via `append_audit_event()` (migration 3), and
  `GET /clinics/{id}/audit-log/verify`. Check-in submit and message send are audited.
  Known gap: the business row and its audit row are separate writes, so a crash
  between them leaves an unaudited row (the request returns 500 and logs it).
  Not audited yet: mark-read.

Not built: rolling summaries, consent/AI-draft audit events.
Tests and lint (`pytest`, `ruff`) run in CI.

## Hard constraints (not negotiable without a product conversation first)

- **No hospital chart or ConnectingOntario data, at all, in any form.** Not a v1
  scope note — an explicit line Sarah drew. If a feature seems to need hospital data,
  stop and raise it rather than building toward it.
- **Structured check-ins, not free text, for the patient side.** Messages remain
  free-form text from both sides (decision 5 in decisions-and-open-questions.md);
  check-ins are dropdown + checkboxes only, because many patients struggle to
  describe symptoms in words.
- **AI never auto-sends.** Drafts are reviewed and approved by the clinician; the
  audit log records who approved what, when, from which protocol/template version.
- **Inference location is still an open question** (local vs. Canadian-region
  zero-retention vs. minimal-fact cloud) — see data-catalogue.md's open items. Don't
  wire up a specific LLM vendor without checking whether that decision has been made
  since this doc was written.
- **Synthetic data only** until the privacy review is done. Nothing in this service
  should be pointed at a real patient yet.

## Near-term backlog (roughly in order)

1. ~~Check-in intake endpoint + link-token validation.~~ Done.
2. ~~Red-flag rule engine~~ Placeholder done; needs Sarah's list (start with a hardcoded rule set; make it clinician-editable
   later, once there's a clinician using it).
3. ~~Message send/receive endpoints~~ Done, both sides.
4. Rolling summary generation, with per-line provenance links back to source messages.
5. ~~Audit log writer~~ Done for check-ins and messages. Wire every new write through
   it (summaries, consent, AI-draft approvals) rather than bolting it on later.

## Reference

- `../../claude/data-catalogue.md` — the full data classification (what's stored,
  where, how long, at what sensitivity). D29 (check-in answers), D6/D7 (messages),
  D16/D17 (audit log) are the ones this service touches first.
- `../../claude/decisions-and-open-questions.md` — product decisions and what's still
  open. Read the "Decisions" section before assuming how something should behave.
