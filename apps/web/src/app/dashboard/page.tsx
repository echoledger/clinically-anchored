"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { AppHeader } from "@/components/app-header";
import { ApiError } from "@/lib/api";
import { Patient, QueueItem, createPatient, getQueue, listPatients } from "@/lib/clinician-api";
import { timeAgo } from "@/lib/format";
import { useClinician } from "@/lib/use-clinician";
import { usePolling } from "@/lib/use-polling";

const REFRESH_MS = 15000;

export default function DashboardPage() {
  const clinician = useClinician();
  if (clinician.status === "loading") return <p className="p-6 text-zinc-600">Loading...</p>;
  if (clinician.status === "error") return <p className="p-6 text-red-600">{clinician.message}</p>;
  return (
    <>
      <AppHeader clinicName={clinician.clinic.name} email={clinician.email} />
      <Dashboard clinicId={clinician.clinic.id} />
    </>
  );
}

function Dashboard({ clinicId }: { clinicId: string }) {
  const fetchAll = useCallback(
    async () => {
      const [queue, patients] = await Promise.all([getQueue(clinicId), listPatients(clinicId)]);
      return { queue, patients };
    },
    [clinicId],
  );
  const { data, failed } = usePolling(fetchAll, REFRESH_MS);
  const queue: QueueItem[] | null = data?.queue ?? null;
  const patients: Patient[] | null = data?.patients ?? null;
  const error = failed ? "Couldn't refresh. Retrying..." : null;

  return (
    <main className="mx-auto max-w-3xl space-y-8 px-4 py-6">
      {error && <p className="text-sm text-amber-700">{error}</p>}

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">
          Needs attention
        </h2>
        {queue === null ? (
          <p className="text-zinc-600">Loading...</p>
        ) : queue.length === 0 ? (
          <p className="rounded-lg border border-dashed border-zinc-300 p-4 text-zinc-600">
            Nothing waiting. New check-ins and patient messages will show up here.
          </p>
        ) : (
          <ul className="space-y-2">
            {queue.map((item) => (
              <li key={item.patient_id}>
                <Link
                  href={`/dashboard/patients/${item.patient_id}`}
                  className={`block rounded-lg border p-4 hover:bg-zinc-50 ${
                    item.has_red_flag ? "border-red-300 bg-red-50/50" : "border-zinc-200 bg-white"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <p className="font-medium text-zinc-900">{item.patient_name ?? "Unknown"}</p>
                    <span className="shrink-0 text-xs text-zinc-500">
                      {timeAgo(item.last_activity_at)}
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2 text-xs">
                    {item.has_red_flag && (
                      <span className="rounded-full bg-red-600 px-2 py-0.5 font-medium text-white">
                        Red flag
                      </span>
                    )}
                    {item.unreviewed_check_ins > 0 && (
                      <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-zinc-700">
                        {item.unreviewed_check_ins} check-in
                        {item.unreviewed_check_ins > 1 ? "s" : ""} to review
                      </span>
                    )}
                    {item.unread_messages > 0 && (
                      <span className="rounded-full bg-blue-100 px-2 py-0.5 text-blue-800">
                        {item.unread_messages} unread message
                        {item.unread_messages > 1 ? "s" : ""}
                      </span>
                    )}
                    {item.latest_post_op_day !== null && (
                      <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-zinc-700">
                        Post-op day {item.latest_post_op_day}
                      </span>
                    )}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">
          All patients
        </h2>
        <AddPatientForm clinicId={clinicId} />
        {patients === null ? null : patients.length === 0 ? (
          <p className="mt-3 text-zinc-600">No patients yet. Add one above.</p>
        ) : (
          <ul className="mt-3 divide-y divide-zinc-200 rounded-lg border border-zinc-200 bg-white">
            {patients.map((p) => (
              <li key={p.id}>
                <Link
                  href={`/dashboard/patients/${p.id}`}
                  className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-zinc-50"
                >
                  <span className="font-medium text-zinc-900">{p.full_name}</span>
                  <span className="truncate text-sm text-zinc-500">{p.contact}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

function AddPatientForm({ clinicId }: { clinicId: string }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const patient = await createPatient(clinicId, {
        full_name: name,
        contact: contact || null,
      });
      router.push(`/dashboard/patients/${patient.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't add this patient.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-2 sm:flex-row">
      <input
        required
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Patient name (use a fake name for now)"
        className="min-w-0 flex-1 rounded-md border border-zinc-300 px-3 py-2 text-base"
      />
      <input
        value={contact}
        onChange={(e) => setContact(e.target.value)}
        placeholder="Phone or email (optional)"
        className="min-w-0 flex-1 rounded-md border border-zinc-300 px-3 py-2 text-base"
      />
      <button
        type="submit"
        disabled={busy || !name.trim()}
        className="rounded-md bg-zinc-900 px-4 py-2 text-base font-medium text-white disabled:opacity-60"
      >
        Add patient
      </button>
      {error && <p className="text-sm text-red-600 sm:basis-full">{error}</p>}
    </form>
  );
}
