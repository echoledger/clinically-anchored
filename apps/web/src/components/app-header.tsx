"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

export function AppHeader({ clinicName, email }: { clinicName: string; email: string | null }) {
  const router = useRouter();
  return (
    <header className="border-b border-zinc-200 bg-white">
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-4 py-3">
        <Link href="/dashboard" className="min-w-0">
          <p className="truncate text-sm font-semibold text-zinc-900">{clinicName}</p>
          <p className="truncate text-xs text-zinc-500">Clinically Anchored</p>
        </Link>
        <div className="flex items-center gap-3">
          {email && <span className="hidden truncate text-xs text-zinc-500 sm:block">{email}</span>}
          <button
            onClick={async () => {
              await supabase.auth.signOut();
              router.replace("/login");
            }}
            className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm text-zinc-700 hover:bg-zinc-50"
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
