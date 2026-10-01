# web — service brief

Read this before touching `apps/web`. It's the product context a fresh thread needs;
`apps/web/README.md` has the dev-loop commands.

## What this app owns

`apps/web` (Next.js, TypeScript, deployed to Vercel) is the only user-facing surface.
Two distinct audiences, likely two distinct flows:

- **The clinician dashboard.** The card-based view described in the "Overview" doc:
  one card per patient, a badge for post-op day (or a countdown before surgery), a
  second pale badge for total time in care, the rolling AI-written summary with
  "still-open items" (questions asked but never answered, questions asked but not yet
  answered by the clinician), the last few messages with read receipts, and a
  contact/time log. Everything the model writes links back to its source message —
  the clinician should never have to trust a summary line without being able to check
  it in one tap. This view is meant for an iPad or phone first, not a desktop-first
  layout.
- **The patient check-in flow.** Reached via a signed link (SMS/email), not a login —
  patients are not Supabase Auth users. Pick a procedure from a dropdown, answer
  symptom checkboxes. Not a chat interface; structured input only, because many
  patients struggle to describe symptoms in free text (this was Sarah's explicit
  preference). Patients can still send free-form messages once in the thread —
  the structured check-in and free-text messaging are complementary, not one gating
  the other.

## The one rule that matters most

This app never imports server code from `apps/api` directly, and never talks to
Postgres directly except through Supabase Auth (for clinician login) and whatever
Supabase gives you client-side for auth session handling. Everything else — check-ins,
messages, summaries, red-flag state — goes through `apps/api` over HTTP, using a
client generated from its OpenAPI schema. This is what keeps this app and the api
service independently deployable and, if it's ever needed, independently split into
their own repos without a rewrite.

## Current state

A first, basic trial UI for reviewing the clinician workflow (not the card-based
dashboard from the Overview doc yet):

- `/login`: clinician email + password via Supabase Auth (session handling only; all data
  goes through `apps/api`).
- `/dashboard`: "Needs attention" queue (red flags first), add patient, all patients.
- `/dashboard/patients/[id]`: copy a check-in or messages link for the patient, review
  check-ins, read and reply to the message thread.
- `/check-in?token=` (patient, structured check-in) and `/messages?token=` (patient chat).
  Patients are link-only for now; patient sign-in comes before real patients.

Uses the user's first clinic. Polls the api every 10-15s (no realtime yet). `shadcn/ui`
was not initialized.

Env (see `.env.example`): `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_SUPABASE_URL`,
`NEXT_PUBLIC_SUPABASE_ANON_KEY` (public values; the anon key is safe in the browser).
The build tolerates them being unset (sign-in then fails at runtime).

Vercel: Root Directory `apps/web`. On the api side, set `WEB_BASE_URL` (patient link
origin) and add the Vercel origin to `ALLOWED_ORIGINS`.

## Deploying (Vercel + Railway)

1. **Vercel**: import the GitHub repo, set **Root Directory** to `apps/web`, and add
   `NEXT_PUBLIC_API_URL` (the Railway api URL), `NEXT_PUBLIC_SUPABASE_URL` and
   `NEXT_PUBLIC_SUPABASE_ANON_KEY` *before* the first build -- `NEXT_PUBLIC_*` values are
   baked in at build time, so changing them needs a redeploy. The anon key is public by
   design (Vercel warns about it; keep the `NEXT_PUBLIC_` name and mark it as config). Never
   put the `service_role` key here.
2. **Railway** (api service): set `WEB_BASE_URL` and `ALLOWED_ORIGINS` to the Vercel origin
   (no trailing slash). Without `WEB_BASE_URL` the "copy link" buttons return 503; without
   `ALLOWED_ORIGINS` the browser blocks every call to the api (CORS).
3. Vercel builds every pushed branch as a Preview and `main` as Production. A preview's
   origin differs from production's, so it also needs adding to `ALLOWED_ORIGINS` to work
   against the api.
4. Give each clinician a Supabase Auth user (Auto Confirm) plus a `clinic_members` row.

## Design references already agreed

The Overview doc (Claude Docs artifact, "Patient Messaging Tool — Overview") has
three mocked-up cards worth pulling up before building the dashboard — they were
reviewed and are the current best guess at layout, not final. Two open questions from
that doc that are still genuinely undecided, worth resolving before or during build
rather than picking silently:

- **Expanded vs. collapsed card as the default** (Card A vs. Card B in the mockups).
- **Whether patients get a free-text message box, or checkboxes only** — the Overview
  doc built it with a message box for both sides; Sarah's email leaned toward
  checkboxes-only for patients. Decision 5 in decisions-and-open-questions.md resolved
  this toward free text + structured layer, but confirm this hasn't shifted before
  building the patient-side input.

## Near-term backlog (roughly in order)

1. Clinician auth (Supabase Auth, email/magic link — no patient-facing auth needed
   yet since patients use signed links, not accounts).
2. The card component itself, static first (real data wiring depends on api
   endpoints existing).
3. Patient check-in flow: procedure dropdown + checkbox form, reachable via a token
   in the URL.
4. Wire cards to real api data once the corresponding endpoints exist.
5. Message thread view with read receipts.

## Reference

- `../../claude/decisions-and-open-questions.md` — read the "Decisions" section,
  especially 5 (messaging model) and 7 (v1 scope), before assuming UI behavior.
