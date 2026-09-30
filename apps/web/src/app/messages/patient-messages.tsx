"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";
import { ApiError, request } from "@/lib/api";
import { clock } from "@/lib/format";
import { usePolling } from "@/lib/use-polling";

interface Message {
  id: string;
  sender: "clinician" | "patient" | "system";
  body: string;
  created_at: string;
}

const REFRESH_MS = 10000;

export function PatientMessages() {
  const token = useSearchParams().get("token") ?? "";
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const q = `?token=${encodeURIComponent(token)}`;
  const fetchThread = useCallback(
    () => (token ? request<Message[]>(`/messages${q}`) : Promise.resolve([] as Message[])),
    [token, q],
  );
  const { data: thread, failed, reload } = usePolling(fetchThread, REFRESH_MS);

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    try {
      await request<Message>(`/messages${q}`, {
        method: "POST",
        body: JSON.stringify({ body: text.trim() }),
      });
      setText("");
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't send your message.");
    } finally {
      setBusy(false);
    }
  }

  if (!token) return <p className="text-red-600">This link is missing its token.</p>;
  if (failed && thread === null) {
    return <p className="text-red-600">Couldn&apos;t load your messages. The link may have expired.</p>;
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex-1 space-y-2">
        {thread === null ? (
          <p className="text-zinc-600">Loading...</p>
        ) : thread.length === 0 ? (
          <p className="text-zinc-600">No messages yet. Write one below.</p>
        ) : (
          thread.map((m) => (
            <div key={m.id} className={m.sender === "patient" ? "text-right" : "text-left"}>
              <div
                className={`inline-block max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-left text-sm ${
                  m.sender === "patient" ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-900"
                }`}
              >
                {m.body}
              </div>
              <p className="mt-0.5 text-xs text-zinc-400">
                {m.sender === "patient" ? "You" : "Care team"} &middot; {clock(m.created_at)}
              </p>
            </div>
          ))
        )}
      </div>
      <form onSubmit={handleSend} className="sticky bottom-0 mt-4 border-t border-zinc-200 bg-white pt-3">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          maxLength={5000}
          placeholder="Write a message..."
          className="block w-full rounded-md border border-zinc-300 px-3 py-2 text-base"
        />
        {error && <p className="mt-1 text-sm text-red-600">{error}</p>}
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="mt-2 w-full rounded-md bg-zinc-900 px-4 py-2.5 text-base font-medium text-white disabled:opacity-60"
        >
          {busy ? "Sending..." : "Send"}
        </button>
      </form>
    </div>
  );
}
