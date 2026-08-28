// The one way this app talks to Frappe.
//
// POST for everything, including reads. Frappe accepts either, and a single shape means the CSRF
// header is applied in exactly one place rather than being a thing each caller remembers. Reads
// are cheap here anyway -- the endpoints this SPA uses (document_guide.get_guide,
// folt_tasks.my_tasks) each answer a whole screen in one call.

import { boot } from "./boot";

export class FrappeError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly excType: string | null,
    readonly serverMessages: string[],
  ) {
    super(message);
    this.name = "FrappeError";
  }
}

/** `_server_messages` arrives as a JSON string of JSON strings. */
function serverMessages(payload: Record<string, unknown>): string[] {
  const raw = payload?._server_messages;
  if (typeof raw !== "string") return [];
  try {
    return (JSON.parse(raw) as string[]).map((entry) => {
      try {
        return (JSON.parse(entry) as { message?: string }).message ?? entry;
      } catch {
        return entry;
      }
    });
  } catch {
    return [];
  }
}

export async function call<T = unknown>(
  method: string,
  args: Record<string, unknown> = {},
): Promise<T> {
  const response = await fetch(`/api/method/${method}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      // Frappe skips the check for Guest, so this only matters once logged in -- which is always,
      // because www/folt.py redirects Guests to the login page before this bundle ever loads.
      "X-Frappe-CSRF-Token": boot.csrf_token,
      Accept: "application/json",
    },
    // Same origin as the document: the page is served by frappe on :8080 even in dev, so the
    // session cookie rides along and there is no CORS preflight on the API.
    credentials: "same-origin",
    body: JSON.stringify(args),
  });

  const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;

  if (!response.ok) {
    const messages = serverMessages(payload);
    throw new FrappeError(
      messages[0] ?? (payload.exception as string) ?? `HTTP ${response.status}`,
      response.status,
      (payload.exc_type as string) ?? null,
      messages,
    );
  }

  return payload.message as T;
}
