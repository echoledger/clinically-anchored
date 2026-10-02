"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api";
import { Message, Summary, generateSummary } from "@/lib/clinician-api";
import { clock, timeAgo } from "@/lib/format";

/** On-demand AI summary of the thread. Every line cites the messages it came from;
 *  the api has already verified each citation against this patient's thread, and
 *  here each one is a link that jumps to (and highlights) that message.
 *
 *  The summary lives only in this component's state: the api doesn't store it, and
 *  it holds patient content, so it is not kept in browser storage either. */
export function SummarySection({
  clinicId,
  patientId,
  thread,
  onJump,
}: {
  clinicId: string;
  patientId: string;
  thread: Message[] | null;
  onJump: (messageId: string) => void;
}) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleGenerate() {
    setBusy(true);
    setError(null);
    try {
      setSummary(await generateSummary(clinicId, patientId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't summarise this thread.");
    } finally {
      setBusy(false);
    }
  }

  const byId = new Map((thread ?? []).map((m) => [m.id, m]));
  const newer = summary
    ? (thread ?? []).filter((m) => new Date(m.created_at) > new Date(summary.generated_at)).length
    : 0;

  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">
        Summary
      </h2>
      <div className="rounded-lg border border-zinc-200 bg-white p-4">
        {summary === null ? (
          <p className="text-sm text-zinc-600">
            Ask the AI to summarise this conversation. Every line links to the messages it came
            from, so you can check it.
          </p>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
              <span className="rounded-full bg-blue-100 px-2 py-0.5 font-medium text-blue-800">
                AI summary
              </span>
              <span className="text-zinc-500">
                {timeAgo(summary.generated_at)} &middot; covers {summary.covers_messages}{" "}
                {summary.covers_messages === 1 ? "message" : "messages"}
              </span>
            </div>

            {newer > 0 && (
              <p className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
                {newer} new {newer === 1 ? "message has" : "messages have"} arrived since this
                summary was written.
              </p>
            )}
            {summary.truncated && (
              <p className="mb-3 text-sm text-zinc-600">
                Only the most recent {summary.covers_messages} messages are covered; older ones
                are left out.
              </p>
            )}

            <ul className="space-y-3">
              {summary.lines.map((line, i) => (
                <li key={i} className="text-sm text-zinc-900">
                  <span>{line.text}</span>{" "}
                  <span>
                    {line.citations.map((id) => (
                      <CitationLink key={id} id={id} message={byId.get(id) ?? null} onJump={onJump} />
                    ))}
                  </span>
                </li>
              ))}
            </ul>

            <p className="mt-3 text-xs text-zinc-400">
              Written by AI ({summary.model_id}, {summary.prompt_version}). Check anything
              important against the linked messages.
            </p>
          </>
        )}

        {error && (
          <p className="mt-3 text-sm text-red-600">
            {error}
            {summary && " The summary above is the previous one."}
          </p>
        )}
        <button
          onClick={handleGenerate}
          disabled={busy || thread === null || thread.length === 0}
          className="mt-3 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
        >
          {busy ? "Summarising..." : summary ? "Refresh summary" : "Summarise this conversation"}
        </button>
        {thread !== null && thread.length === 0 && (
          <p className="mt-2 text-xs text-zinc-500">There are no messages to summarise yet.</p>
        )}
      </div>
    </section>
  );
}

/** One citation: a link to the source message in the thread below. */
function CitationLink({
  id,
  message,
  onJump,
}: {
  id: string;
  message: Message | null;
  onJump: (messageId: string) => void;
}) {
  if (!message) {
    // The api verified this id, so this only happens while the thread is still loading.
    return (
      <span className="mr-1 inline-block rounded border border-dashed border-zinc-300 px-1.5 py-0.5 text-xs text-zinc-400">
        message not loaded
      </span>
    );
  }
  const who = message.sender === "clinician" ? "You" : "Patient";
  return (
    <a
      href={`#message-${id}`}
      onClick={(e) => {
        e.preventDefault();
        onJump(id);
      }}
      title={message.body.length > 120 ? `${message.body.slice(0, 120)}...` : message.body}
      className="mr-1 inline-block rounded border border-zinc-300 px-1.5 py-0.5 text-xs text-blue-700 hover:bg-blue-50"
    >
      {who} &middot; {clock(message.created_at)}
    </a>
  );
}
