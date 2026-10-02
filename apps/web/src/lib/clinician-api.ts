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

export type DraftStatus = "pending" | "approved" | "edited" | "rejected";
/** An AI-written reply to a patient message. Nothing is sent until a clinician decides. */
export interface Draft {
  id: string;
  patient_id: string;
  source_message_id: string; // the patient message this answers
  draft_text: string; // exactly what the model wrote; never changes
  model_id: string;
  prompt_version: string;
  status: DraftStatus;
  final_text: string | null; // what was actually sent (approved / edited)
  sent_message_id: string | null;
  created_at: string;
  decided_at: string | null;
}
export interface DraftDecision {
  draft: Draft;
  message: Message | null; // the message that was sent; null when rejected
}
export interface SummaryLine {
  text: string;
  citations: string[]; // ids of messages in this patient's thread; verified by the api
}
export interface Summary {
  summary_id: string;
  patient_id: string;
  generated_at: string;
  model_id: string;
  prompt_version: string;
  lines: SummaryLine[];
  covers_messages: number;
  truncated: boolean; // older messages were left out
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

// AI drafts: generate stores a pending draft and sends nothing; only approve / edit
// send a message (reject sends nothing). Only owners and clinicians may decide.
export const listDrafts = (clinic: string, patient: string) =>
  authed<Draft[]>(`/clinics/${clinic}/patients/${patient}/drafts`);
export const generateDraft = (clinic: string, patient: string) =>
  post<Draft>(`/clinics/${clinic}/patients/${patient}/drafts`);
export const approveDraft = (clinic: string, id: string) =>
  post<DraftDecision>(`/clinics/${clinic}/drafts/${id}/approve`);
export const editDraft = (clinic: string, id: string, finalText: string) =>
  post<DraftDecision>(`/clinics/${clinic}/drafts/${id}/edit`, { final_text: finalText });
export const rejectDraft = (clinic: string, id: string) =>
  post<DraftDecision>(`/clinics/${clinic}/drafts/${id}/reject`);

// On-demand summary. Not stored by the api: each call regenerates (and costs a call).
export const generateSummary = (clinic: string, patient: string) =>
  post<Summary>(`/clinics/${clinic}/patients/${patient}/summaries`);
