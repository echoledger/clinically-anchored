// Calls to apps/api that need a signed-in clinician: the Supabase access token
// is sent as a bearer token and the api checks clinic membership.
import { request } from "./api";
import { supabase } from "./supabase";

export interface Clinic {
  id: string;
  name: string;
  role: string;
}
export interface Me {
  user_id: string;
  email: string | null;
  clinics: Clinic[];
}
export interface QueueItem {
  patient_id: string;
  patient_name: string | null;
  has_red_flag: boolean;
  unreviewed_check_ins: number;
  unread_messages: number;
  latest_check_in_id: string | null;
  latest_post_op_day: number | null;
  last_activity_at: string;
}
export interface Patient {
  id: string;
  full_name: string;
  contact: string | null;
  created_at: string;
}
export interface CheckInDetail {
  id: string;
  patient_id: string;
  patient_name: string | null;
  procedure_name: string | null;
  post_op_day: number | null;
  answers: Record<string, unknown>;
  is_red_flag: boolean;
  created_at: string;
  reviewed_at: string | null;
}
export interface Message {
  id: string;
  sender: "clinician" | "patient" | "system";
  body: string;
  read_at: string | null;
  created_at: string;
}
export interface IssuedLink {
  scope: "checkin" | "messages";
  url: string;
  expires_at: string;
}

async function authed<T>(path: string, init?: RequestInit): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  return request<T>(path, {
    ...init,
    headers: { ...init?.headers, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
}

const post = <T>(path: string, body?: unknown) =>
  authed<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const getMe = () => authed<Me>("/me");
export const getQueue = (clinic: string) => authed<QueueItem[]>(`/clinics/${clinic}/queue`);
export const listPatients = (clinic: string) => authed<Patient[]>(`/clinics/${clinic}/patients`);
export const createPatient = (clinic: string, body: { full_name: string; contact: string | null }) =>
  post<Patient>(`/clinics/${clinic}/patients`, body);
export const issueLink = (clinic: string, patient: string, scope: "checkin" | "messages") =>
  post<IssuedLink>(`/clinics/${clinic}/patients/${patient}/links/${scope}`);
export const listCheckIns = (clinic: string, patient: string) =>
  authed<CheckInDetail[]>(`/clinics/${clinic}/check-ins?patient_id=${patient}&limit=50`);
export const reviewCheckIn = (clinic: string, id: string) =>
  post<CheckInDetail>(`/clinics/${clinic}/check-ins/${id}/review`);
export const listThread = (clinic: string, patient: string) =>
  authed<Message[]>(`/clinics/${clinic}/patients/${patient}/messages`);
export const sendMessage = (clinic: string, patient: string, body: string) =>
  post<Message>(`/clinics/${clinic}/patients/${patient}/messages`, { body });
export const markRead = (clinic: string, id: string) =>
  post<Message>(`/clinics/${clinic}/messages/${id}/read`);
