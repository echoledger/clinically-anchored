"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError } from "./api";
import { Clinic, getMe } from "./clinician-api";
import { supabase } from "./supabase";

export type ClinicianState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; email: string | null; clinic: Clinic };

/** Requires a signed-in clinician (else redirects to /login) and resolves the
 *  clinic to work in. For the trial we use the user's first clinic. */
export function useClinician(): ClinicianState {
  const router = useRouter();
  const [state, setState] = useState<ClinicianState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const { data } = await supabase.auth.getSession();
      if (!data.session) {
        router.replace("/login");
        return;
      }
      try {
        const me = await getMe();
        if (cancelled) return;
        if (me.clinics.length === 0) {
          setState({ status: "error", message: "Your account isn't linked to a clinic yet." });
        } else {
          setState({ status: "ready", email: me.email, clinic: me.clinics[0] });
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          await supabase.auth.signOut();
          router.replace("/login");
          return;
        }
        setState({ status: "error", message: "Couldn't reach the server. Try again shortly." });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  return state;
}
