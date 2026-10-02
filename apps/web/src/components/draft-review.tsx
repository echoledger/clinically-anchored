"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api";
import {
  Draft,
  Message,
  approveDraft,
  editDraft,
  generateDraft,
  rejectDraft,
} from "@/lib/clinician-api";
import { clock, timeAgo } from "@/lib/format";

// Matches the api's DECIDER_ROLES: delegates can read and request drafts, not decide.
const DECIDER_ROLES = ["owner", "clinician"];

const OUTCOME: Record<Draft["status"], string> = {
  pending: "Waiting",
  approved: "Sent as written",
  edited: "Edited, then sent",
  rejected: "Rejected, nothing sent",
};

/** AI-drafted replies. A draft sits here, beside the patient message it answers,
 *  until a clinician approves it, edits it and sends the edit, or rejects it. */
export function DraftsSection({
  clinicId,
  patientId,
  role,
  drafts,
  thread,
  onChanged,
}: {
  clinicId: string;
  patientId: string;
  role: string;
  drafts: Draft[] | null; // null = not loaded or unavailable
  thread: Message[] | null;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canDecide = DECIDER_ROLES.includes(role);
  const pending = (drafts ?? []).filter((d) => d.status === "pending");
  const decided = (drafts ?? []).filter((d) => d.status !== "pending").slice(0, 10);
  const latestPatientMessage = [...(thread ?? [])].reverse().find((m) => m.sender === "patient");
  const alreadyDrafted =
    latestPatientMessage !== undefined &&
    pending.some((d) => d.source_message_id === latestPatientMessage.id);

  async function handleGenerate() {
    setBusy(true);
    setError(null);
    try {
      await generateDraft(clinicId, patientId);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't draft a reply.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">
        Draft reply
      </h2>

      {drafts === null && (
        <p className="rounded-lg border border-dashed border-zinc-300 p-4 text-sm text-zinc-600">
          AI drafts aren&rsquo;t available right now.
        </p>
      )}

      {drafts !== null && (
        <div className="space-y-3">
          {pending.map((d) => (
            <PendingDraft
              key={d.id}
              draft={d}
              source={thread?.find((m) => m.id === d.source_message_id) ?? null}
              clinicId={clinicId}
              canDecide={canDecide}
              onChanged={onChanged}
            />
          ))}

          {!alreadyDrafted && (
            <div className="rounded-lg border border-zinc-200 bg-white p-4">
              <p className="text-sm text-zinc-600">
                {latestPatientMessage
                  ? "Ask the AI to draft a reply to the patient’s latest message. You review it first; nothing is sent until you approve."
                  : "When the patient writes, you can ask the AI to draft a reply here."}
              </p>
              {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
              <button
                onClick={handleGenerate}
                disabled={busy || !latestPatientMessage}
                className="mt-3 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
              >
                {busy ? "Drafting..." : "Draft a reply with AI"}
              </button>
            </div>
          )}

          {decided.length > 0 && (
            <details className="rounded-lg border border-zinc-200 bg-white">
              <summary className="cursor-pointer px-4 py-3 text-sm text-zinc-700">
                Earlier drafts ({decided.length})
              </summary>
              <ul className="space-y-3 border-t border-zinc-200 p-4">
                {decided.map((d) => (
                  <DecidedDraft key={d.id} draft={d} />
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </section>
  );
}

function PendingDraft({
  draft,
  source,
  clinicId,
  canDecide,
  onChanged,
}: {
  draft: Draft;
  source: Message | null;
  clinicId: string;
  canDecide: boolean;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(draft.draft_text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // An edit must actually change the draft (otherwise approve it as written).
  const edited = text.trim() !== "" && text.trim() !== draft.draft_text.trim();

  async function run(action: () => Promise<unknown>, fallback: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : fallback);
      // Someone else decided first: refresh so this card reflects what happened.
      if (err instanceof ApiError && err.status === 409) onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50/40 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-full bg-blue-100 px-2 py-0.5 font-medium text-blue-800">
          AI draft
        </span>
        <span className="text-zinc-500">
          {timeAgo(draft.created_at)} &middot; {draft.model_id} &middot; {draft.prompt_version}
        </span>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
            Patient wrote
          </p>
          <div className="rounded-lg border border-zinc-200 bg-white p-3 text-sm text-zinc-900">
            {source ? (
              <>
                <p className="whitespace-pre-wrap">{source.body}</p>
                <p className="mt-1 text-xs text-zinc-400">{clock(source.created_at)}</p>
              </>
            ) : (
              <p className="text-zinc-500">Original message not loaded yet.</p>
            )}
          </div>
        </div>

        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
            {editing ? "Your edit" : "Draft reply"}
          </p>
          {editing ? (
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={6}
              maxLength={5000}
              className="block w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-base"
            />
          ) : (
            <div className="whitespace-pre-wrap rounded-lg border border-zinc-200 bg-white p-3 text-sm text-zinc-900">
              {draft.draft_text}
            </div>
          )}
        </div>
      </div>

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      {canDecide ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {editing ? (
            <>
              <button
                onClick={() => run(() => editDraft(clinicId, draft.id, text.trim()), "Couldn't send that edit.")}
                disabled={busy || !edited}
                className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
              >
                {busy ? "Sending..." : "Send edited reply"}
              </button>
              <button
                onClick={() => {
                  setEditing(false);
                  setText(draft.draft_text);
                }}
                disabled={busy}
                className="rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-700 disabled:opacity-60"
              >
                Cancel edit
              </button>
              {!edited && (
                <span className="text-xs text-zinc-500">
                  Change the text to send an edit, or cancel and approve it as written.
                </span>
              )}
            </>
          ) : (
            <>
              <button
                onClick={() => run(() => approveDraft(clinicId, draft.id), "Couldn't send that reply.")}
                disabled={busy}
                className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
              >
                {busy ? "Sending..." : "Approve and send"}
              </button>
              <button
                onClick={() => setEditing(true)}
                disabled={busy}
                className="rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-700 disabled:opacity-60"
              >
                Edit, then send
              </button>
              <button
                onClick={() => run(() => rejectDraft(clinicId, draft.id), "Couldn't reject that draft.")}
                disabled={busy}
                className="rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-red-700 disabled:opacity-60"
              >
                Reject
              </button>
            </>
          )}
        </div>
      ) : (
        <p className="mt-3 text-sm text-zinc-600">
          Only a clinician can approve, edit or reject a draft.
        </p>
      )}
    </div>
  );
}

function DecidedDraft({ draft }: { draft: Draft }) {
  const edited = draft.status === "edited";
  return (
    <li className="text-sm">
      <p className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
        <span
          className={`rounded-full px-2 py-0.5 font-medium ${
            draft.status === "rejected"
              ? "bg-zinc-100 text-zinc-700"
              : "bg-green-100 text-green-800"
          }`}
        >
          {OUTCOME[draft.status]}
        </span>
        {draft.decided_at ? clock(draft.decided_at) : clock(draft.created_at)}
      </p>
      <p className="mt-1 whitespace-pre-wrap text-zinc-700">
        <span className="text-zinc-400">AI wrote: </span>
        {draft.draft_text}
      </p>
      {edited && draft.final_text && (
        <p className="mt-1 whitespace-pre-wrap text-zinc-900">
          <span className="text-zinc-400">Sent instead: </span>
          {draft.final_text}
        </p>
      )}
    </li>
  );
}
