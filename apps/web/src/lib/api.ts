// Thin fetch wrapper for the api service. This is a hand-written stand-in
// for the OpenAPI-generated client mentioned in the repo README -- codegen
// isn't wired up yet, but nothing here should be called with anything
// other than the api's actual response shapes.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export interface Procedure {
  id: string;
  name: string;
}

export interface CheckInContext {
  clinic_id: string;
  procedures: Procedure[];
}

export interface CheckInOut {
  id: string;
  clinic_id: string;
  patient_id: string;
  procedure_id: string | null;
  post_op_day: number | null;
  answers: Record<string, boolean>;
  is_red_flag: boolean;
  created_at: string;
}

export function getCheckInContext(token: string): Promise<CheckInContext> {
  return request(`/check-ins/context?token=${encodeURIComponent(token)}`);
}

export function submitCheckIn(
  token: string,
  body: { procedure_id: string | null; post_op_day: number | null; answers: Record<string, boolean> },
): Promise<CheckInOut> {
  return request(`/check-ins?token=${encodeURIComponent(token)}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
