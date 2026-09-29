import { Suspense } from "react";
import { CheckInForm } from "./check-in-form";

export default function CheckInPage() {
  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6 py-12">
      <h1 className="mb-6 text-xl font-semibold text-zinc-900">Post-op check-in</h1>
      <Suspense fallback={<p className="text-zinc-600">Loading...</p>}>
        <CheckInForm />
      </Suspense>
    </div>
  );
}
