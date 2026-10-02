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

Built (on `main`):

- Check-in intake: `POST /check-ins`, `GET /check-ins/context`, signed/expiring link
  tokens (`core/security.py`), dev-only `POST /dev/check-in-links`.
- Red-flag rules (`core/rules.py`) -- **placeholder logic**, not Sarah's real list.
- Clinician auth (`core/auth.py`): Supabase JWT + `clinic_members` check per clinic.
- Messages: clinician side (list thread, send, mark read; JWT auth) and patient side
  (`GET`/`POST /messages?token=`; sender forced to `patient`). Both send paths share one
  insert-and-audit function. Link tokens are scoped: `checkin` (14-day expiry) and
  `messages` (48h, `MESSAGE_LINK_MAX_AGE_SECONDS`); a token only works on routes of its
  own scope. Still bearer links -- anyone holding a messages link can read the thread until
  it expires. Mark-read is audited (`message.read`, first read only).
- Rate limiting on every link-token route (`core/ratelimit.py`, applied in the token
  dependency in `core/security.py`): per patient, sliding 60s window, 60 reads and 10 writes
  per minute (`PATIENT_LINK_READS_PER_MINUTE` / `PATIENT_LINK_WRITES_PER_MINUTE`; 0 disables).
  429 with `Retry-After`. **In-memory, single instance:** counters reset on restart and are
  not shared across instances/workers -- Redis-backed limiting is a Pro-tier job and `hit()`
  is the seam to swap. Keyed by the verified token's patient (shared across their link
  scopes), not by IP: behind Railway's proxy `request.client.host` isn't the real client unless
  uvicorn is told to trust forwarded headers. Invalid tokens aren't counted (rejected by HMAC
  before any DB work). Clinician routes are not rate limited.
- Consent records (`api/consents.py`, migration 5, data-catalogue D12) -- **storage and audit
  only, nothing is gated.** Patient side, any link scope: `GET /consents`, `POST /consents`
  (`consent_type` slug + `consent_text_version`), `POST /consents/{type}/revoke`. Clinician side:
  `GET /clinics/{id}/patients/{pid}/consents` (current `active` types + full history).
  One row per grant; withdrawing sets `revoked_at` on every live grant of that type, a later
  re-grant is a new row. Grants are idempotent per (type, version); a new wording version is
  a new grant. Each grant/withdrawal is audited (`consent.granted` / `consent.revoked`, ref
  type `consent`). **Open, Sarah's call -- deliberately not decided here:** the consent
  wording (lives outside this service, referenced by version), whether any consent blocks
  check-in or messaging, and how many types there are (messaging vs. AI-assisted
  communication per the catalogue). Until the list is settled any well-formed slug is accepted;
  set `CONSENT_TYPES` (comma-separated) to enforce a list. Not built: a clinician-side
  "record a withdrawal on the patient's behalf" route (who may do that is part of the same
  open question).
- Clinician queue (`api/queue.py`): `GET /clinics/{id}/queue` (patients needing attention:
  unreviewed check-ins + unread patient messages, unreviewed red flags first),
  `GET /clinics/{id}/check-ins` (filters: unreviewed / red flag / patient), and
  `POST /clinics/{id}/check-ins/{id}/review` (idempotent, audited as `check_in.reviewed`).
  Migration 4 adds `check_ins.reviewed_at/by`. Queue reads cap at 1000 rows per query.
- Clinic/patient management for the web app: `GET /me`, `GET`/`POST /clinics/{id}/patients`
  (audited `patient.created`), and `POST /clinics/{id}/patients/{pid}/links/{checkin|messages}`
  which returns a copyable URL (audited `link.issued`, token never logged). Link issuance
  stands in for SMS/email delivery during trials; needs `WEB_BASE_URL` outside development.
  Patients stay link-only (no patient sign-in) -- decision for the trial; revisit with Sarah.
- Startup guard: outside `ENVIRONMENT=development` the app refuses to start unless
  `CHECKIN_LINK_SECRET`, `AUDIT_SIGNING_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` are set.
- Audit log writer (`core/audit.py`): salted payload hash, per-clinic hash chain,
  Ed25519 signature, atomic append via `append_audit_event()` (migration 3), and
  `GET /clinics/{id}/audit-log/verify` (checks each row against the public key named by its
  `key_id`; keys other than the current one come from `AUDIT_PUBLIC_KEYS`). Audited events:
  `check_in.submitted`/`.reviewed`, `message.sent`/`.read`, `patient.created`, `link.issued`,
  `consent.granted`/`.revoked`.
  Known gap: the business row and its audit row are separate writes, so a failure
  between them leaves an unaudited row (the request returns 500 and logs it; the row stays).
  Reconciliation is a script, not an endpoint: `python -m clinically_anchored_api.core.reconcile
  [--clinic ID] [--since ISO] [--json]` (needs the api's Supabase env, e.g. `railway run`)
  matches every row against `audit_log` by clinic, `event_type` and `metadata.ref_id`, and
  reports unaudited rows (exit 1) plus, informationally, audit events whose row is gone. It is
  read-only and selects ids/timestamps only; what to do about an orphan is a human decision.
  Links can't orphan: they have no row, and the URL is returned only after its audit write.
  Rows that predate an event type (seed-migration patients, messages read before `message.read`
  was audited) show as unaudited -- scope with `--since`.

- AI wrapper (`core/ai.py`, used only by the drafts routes below): thin AWS Bedrock Converse client
  (boto3, standard credential chain), always `ca-central-1` (fixed in code; `AWS_REGION` is ignored
  so inference can't drift out of Canada), model from `BEDROCK_MODEL_ID` (default Claude Haiku
  4.5). `generate()` takes the caller's `prompt_version`, sends it to Bedrock as request metadata
  and returns it with the model id and token usage, so the *caller* logs all three to the audit
  trail (this module writes no audit events). Per-clinic daily call cap
  (`AI_DAILY_CALL_CAP_PER_CLINIC`, default 200; 0 blocks everything) is checked before Bedrock
  is called and raises `AIDailyCapExceeded`; in-memory, per instance, UTC day, failed calls
  count. Prompt/reply text is never logged. Versioned prompt templates live in
  `clinically_anchored_api/templates/<name>_<version>.md` (`## System` + `## User`, `{{vars}}`),
  loaded with `load_template(name, "v1")`; the two shipped (`draft_reply_v1`, `summary_v1`) are
  **placeholder wording pending clinical review**. A wording change is a new version file.
  Inference location remains a product decision (see the hard constraints below): this wrapper
  assumes Bedrock in ca-central-1.
- AI message drafts (`api/drafts.py`, migration 6, data-catalogue D8): `POST
  /clinics/{id}/patients/{pid}/drafts` asks the model for a reply to a patient message (default:
  the latest) and stores it `pending` -- it sends nothing. `GET .../drafts[?status=]` lists them.
  A draft leaves `pending` only by an explicit clinician action: `POST /clinics/{id}/drafts/{did}/`
  `approve` (sends the draft as written), `edit` (body `{final_text}`; sends the edit, keeps the
  model's original; the edit must differ), or `reject` (sends nothing). The decision is one DB
  function (`decide_message_draft`) so a draft can't be sent twice or marked sent without a
  message; a second decision is a 409. Approve/edit/reject need role `owner` or `clinician`
  (`DECIDER_ROLES`); delegates can read and request drafts. Asking again for a message that
  already has a pending draft returns it without another model call; daily cap -> 429, model
  failure -> 502. Audited: `draft.generated` / `.approved` / `.edited` / `.rejected`, each with
  `model_id` and `prompt_version` in readable metadata (plus token counts on generated, and the
  sent message id on approve/edit), and an ordinary `message.sent` for the message itself. Drafts
  are kept forever: no delete route, and the database refuses deletes and changes to a decided
  draft or to the original text. **Data flow:** generating a draft sends the last 20 messages of
  that patient's thread to Bedrock (ca-central-1). It stays inert until `AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY` are set on Railway (no credentials -> 502), and the synthetic-data-only
  rule still applies. Clinic protocol notes (D13) aren't modelled, so that template variable is
  a fixed placeholder, and the template wording is still pending clinical review.

Not built: rolling summaries.
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
5. ~~Audit log writer~~ Done for check-ins, messages, patients, links, consent and AI drafts.
   Wire every new write through it (summaries) rather than bolting it on later, and add
   its (table, event) pair to `CHECKS` in `core/reconcile.py`.

## Reference

- `../../claude/data-catalogue.md` — the full data classification (what's stored,
  where, how long, at what sensitivity). D29 (check-in answers), D6/D7 (messages),
  D16/D17 (audit log) are the ones this service touches first.
- `../../claude/decisions-and-open-questions.md` — product decisions and what's still
  open. Read the "Decisions" section before assuming how something should behave.
