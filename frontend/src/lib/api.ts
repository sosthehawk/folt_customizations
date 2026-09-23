// The one way this app talks to Frappe.
//
// POST for everything, including reads. Frappe accepts either, and a single shape means the CSRF
// header is applied in exactly one place rather than being a thing each caller remembers.
//
// SERVER MESSAGES ARE HTML AND ARRIVE ON SUCCESS TOO. FoLT's rules are stated in msgprints that
// carry frappe.bold, <br> and get_link_to_form anchors, and several of them land on a save that
// *worked* -- "Withdrawn from 2 committee evaluations", "Another draft on this register". A client
// that reads `_server_messages` only on failure silently drops half of what the server says. So
// `callWithNotices` returns them alongside the result, and FrappeError carries them on failure.
// Rendering them is lib/html.ts's job, which sanitises: messages interpolate participant and
// supplier names, so document data reaches the browser inside them.

import { boot } from "./boot";

export class FrappeError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly excType: string | null,
    readonly serverMessages: string[],
    readonly title: string | null = null,
  ) {
    super(message);
    this.name = "FrappeError";
  }
}

type Parsed = { message: string; title: string | null };

/** `_server_messages` arrives as a JSON string of JSON strings. */
function serverMessages(payload: Record<string, unknown>): Parsed[] {
  const raw = payload?._server_messages;
  if (typeof raw !== "string") return [];
  try {
    return (JSON.parse(raw) as string[]).map((entry) => {
      try {
        const parsed = JSON.parse(entry) as { message?: string; title?: string };
        return { message: parsed.message ?? entry, title: parsed.title ?? null };
      } catch {
        return { message: entry, title: null };
      }
    });
  } catch {
    return [];
  }
}

async function post(method: string, args: Record<string, unknown>) {
  const response = await fetch(`/api/method/${method}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Frappe-CSRF-Token": boot.csrf_token,
      Accept: "application/json",
    },
    credentials: "same-origin",
    body: JSON.stringify(args),
  });
  const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  const messages = serverMessages(payload);

  if (!response.ok) {
    throw new FrappeError(
      messages[0]?.message ?? (payload.exception as string) ?? `HTTP ${response.status}`,
      response.status,
      (payload.exc_type as string) ?? null,
      messages.map((m) => m.message),
      messages[0]?.title ?? null,
    );
  }
  return { message: payload.message, notices: messages.map((m) => m.message) };
}

export async function call<T = unknown>(method: string, args: Record<string, unknown> = {}): Promise<T> {
  return (await post(method, args)).message as T;
}

/** For writes: the result plus every message the server printed while producing it. */
export async function callWithNotices<T = unknown>(
  method: string,
  args: Record<string, unknown> = {},
): Promise<{ result: T; notices: string[] }> {
  const { message, notices } = await post(method, args);
  return { result: message as T, notices };
}

/** Multipart upload to a whitelisted method -- the one call that is not JSON. */
export async function upload<T = unknown>(
  method: string,
  fields: Record<string, string>,
  file: File,
): Promise<{ result: T; notices: string[] }> {
  const body = new FormData();
  for (const [key, value] of Object.entries(fields)) body.append(key, value);
  body.append("file", file, file.name);
  const response = await fetch(`/api/method/${method}`, {
    method: "POST",
    headers: { "X-Frappe-CSRF-Token": boot.csrf_token, Accept: "application/json" },
    credentials: "same-origin",
    body,
  });
  const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  const messages = serverMessages(payload);
  if (!response.ok) {
    throw new FrappeError(
      messages[0]?.message ?? `HTTP ${response.status}`,
      response.status,
      (payload.exc_type as string) ?? null,
      messages.map((m) => m.message),
      messages[0]?.title ?? null,
    );
  }
  return { result: payload.message as T, notices: messages.map((m) => m.message) };
}

export function errorText(error: unknown): string {
  return error instanceof FrappeError ? error.message : String(error);
}
