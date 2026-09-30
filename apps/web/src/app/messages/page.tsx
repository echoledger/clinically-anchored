import { Suspense } from "react";
import { PatientMessages } from "./patient-messages";

export default function MessagesPage() {
  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col px-4 py-6">
      <h1 className="mb-4 text-xl font-semibold text-zinc-900">Messages with your care team</h1>
      <Suspense fallback={<p className="text-zinc-600">Loading...</p>}>
        <PatientMessages />
      </Suspense>
    </div>
  );
}
