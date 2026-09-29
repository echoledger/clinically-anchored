"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ApiError, CheckInContext, getCheckInContext, submitCheckIn } from "@/lib/api";
import { SYMPTOM_OPTIONS } from "@/lib/symptoms";

type Status = "loading" | "ready" | "submitting" | "submitted" | "error";

export function CheckInForm() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState<string | null>(null);
  const [context, setContext] = useState<CheckInContext | null>(null);

  const [procedureId, setProcedureId] = useState<string>("");
  const [postOpDay, setPostOpDay] = useState<string>("");
  const [answers, setAnswers] = useState<Record<string, boolean>>({});

  useEffect(() => {
    // No setState here for the missing-token case -- `token` is already
    // known synchronously from searchParams, so that's handled as a plain
    // render-time check below instead of routed through effect state.
    if (!token) return;
    getCheckInContext(token)
      .then((ctx) => {
        setContext(ctx);
        setStatus("ready");
      })
      .catch((err: unknown) => {
        setStatus("error");
        setError(err instanceof ApiError ? err.message : "Couldn't load this check-in link.");
      });
  }, [token]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("submitting");
    try {
      await submitCheckIn(token, {
        procedure_id: procedureId || null,
        post_op_day: postOpDay ? Number(postOpDay) : null,
        answers,
      });
      setStatus("submitted");
    } catch (err) {
      setStatus("error");
      setError(err instanceof ApiError ? err.message : "Couldn't submit this check-in.");
    }
  }

  if (!token) {
    return <p className="text-red-600">This link is missing its token.</p>;
  }

  if (status === "loading") {
    return <p className="text-zinc-600">Loading your check-in...</p>;
  }

  if (status === "error" && !context) {
    return <p className="text-red-600">{error}</p>;
  }

  if (status === "submitted") {
    return (
      <div className="rounded-lg border border-green-200 bg-green-50 p-6">
        <p className="font-medium text-green-900">Thanks -- your check-in was sent.</p>
        <p className="mt-1 text-sm text-green-800">
          Your care team will follow up if anything needs their attention.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6">
      <div>
        <label htmlFor="procedure" className="mb-1 block text-sm font-medium text-zinc-900">
          Which procedure did you have?
        </label>
        <select
          id="procedure"
          value={procedureId}
          onChange={(e) => setProcedureId(e.target.value)}
          className="w-full rounded-md border border-zinc-300 px-3 py-2 text-zinc-900"
          required
        >
          <option value="" disabled>
            Select one
          </option>
          {context?.procedures.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label htmlFor="post-op-day" className="mb-1 block text-sm font-medium text-zinc-900">
          How many days since your surgery? (optional)
        </label>
        <input
          id="post-op-day"
          type="number"
          min={0}
          value={postOpDay}
          onChange={(e) => setPostOpDay(e.target.value)}
          className="w-full rounded-md border border-zinc-300 px-3 py-2 text-zinc-900"
        />
      </div>

      <fieldset>
        <legend className="mb-2 text-sm font-medium text-zinc-900">
          Are you experiencing any of the following?
        </legend>
        <div className="flex flex-col gap-2">
          {SYMPTOM_OPTIONS.map((opt) => (
            <label key={opt.key} className="flex items-center gap-2 text-zinc-800">
              <input
                type="checkbox"
                checked={Boolean(answers[opt.key])}
                onChange={(e) => setAnswers((prev) => ({ ...prev, [opt.key]: e.target.checked }))}
              />
              {opt.label}
            </label>
          ))}
        </div>
      </fieldset>

      {status === "error" && error && <p className="text-sm text-red-600">{error}</p>}

      <button
        type="submit"
        disabled={status === "submitting"}
        className="rounded-md bg-zinc-900 px-4 py-2 font-medium text-white disabled:opacity-50"
      >
        {status === "submitting" ? "Sending..." : "Send check-in"}
      </button>
    </form>
  );
}
