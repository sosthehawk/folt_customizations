// The live half: a socket when one is available, polling always.
//
// ## Where the client comes from
//
// NOT from package.json. frappe already serves a standalone ESM build of the socket.io client its
// own server is paired with, at /assets/frappe/node_modules/socket.io-client/dist/socket.io.esm.min.js,
// so it is imported from there at runtime: one copy of the library, and a client version that
// matches the server by construction. The cost is that Vite cannot see the import (@vite-ignore).
//
// ## What is listened for
//
// - `notification` -- frappe's own, on every Notification Log for this user. That is how a step
//   landing on somebody arrives (notifications.notify_pending_approvers writes one), so it
//   refreshes the bell and the task list.
// - `doc_update` -- frappe's, on every save of the document the reader has open, after
//   `doc_subscribe` has joined its room (frappe checks read permission before joining).
// - `folt_state_derived` -- FoLT's, from float_lifecycle._apply. The ledger moves a float to
//   Disbursed or Accounted with db.set_value, which never calls notify_update, so doc_update does
//   not fire for exactly the transitions that happen behind the reader's back.
//
// ## Why the poll is not optional
//
// A socket that drops does not announce itself, and locally it may never connect: nginx sets the
// socket's Origin to the site name (folt.localhost) while the browser is on localhost, and
// frappe's socket authenticator refuses a Host/Origin mismatch. A screen that silently stops
// updating is worse than one that never claimed to be live, so the socket only ever shortens the
// wait for a poll that is always running.

import { boot } from "./boot";

const SOCKET_CLIENT = "/assets/frappe/node_modules/socket.io-client/dist/socket.io.esm.min.js";

type Handler = (payload: unknown) => void;
type Socket = {
  on: (event: string, fn: (...args: unknown[]) => void) => void;
  emit: (event: string, ...args: unknown[]) => void;
  disconnect: () => void;
};

const listeners = new Map<string, Set<Handler>>();

let socket: Socket | null = null;
let connected = false;
let pollTimer: number | null = null;
let pollHandler: (() => void) | null = null;
let watched: { doctype: string; name: string } | null = null;

const POLL_IDLE_MS = 60_000;
const POLL_BACKSTOP_MS = 300_000;

export function isLive(): boolean {
  return connected;
}

export function on(event: string, handler: Handler): () => void {
  const set = listeners.get(event) ?? new Set();
  set.add(handler);
  listeners.set(event, set);
  return () => set.delete(handler);
}

function emit(event: string, payload: unknown) {
  for (const handler of listeners.get(event) ?? []) handler(payload);
}

/** Join the open document's room, so a colleague's save refreshes this screen. */
export function watchDoc(doctype: string | null, name: string | null) {
  if (watched && socket && connected) socket.emit("doc_unsubscribe", watched.doctype, watched.name);
  watched = doctype && name ? { doctype, name } : null;
  if (watched && socket && connected) socket.emit("doc_subscribe", watched.doctype, watched.name);
}

function onVisibility() {
  if (document.visibilityState === "visible") {
    pollHandler?.();
    schedulePoll();
  } else if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

export async function start(refresh: () => void) {
  pollHandler = refresh;
  schedulePoll();
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("focus", onVisibility);

  try {
    const mod = await import(/* @vite-ignore */ SOCKET_CLIENT);
    const io = (mod as { io?: unknown }).io as ((url: string, opts: Record<string, unknown>) => Socket) | undefined;
    if (typeof io !== "function") throw new Error("no io export");

    socket = io(`${window.location.origin}/${boot.sitename}`, {
      withCredentials: true,
      transports: ["polling", "websocket"],
      reconnectionDelayMax: 10_000,
    });

    socket.on("connect", () => {
      connected = true;
      if (watched) socket?.emit("doc_subscribe", watched.doctype, watched.name);
      schedulePoll();
      refresh();
    });
    socket.on("disconnect", () => {
      connected = false;
      schedulePoll();
    });
    socket.on("connect_error", () => {
      connected = false;
      schedulePoll();
    });

    socket.on("notification", () => emit("notification", null));
    socket.on("doc_update", (payload: unknown) => emit("doc_update", payload));
    socket.on("folt_state_derived", (payload: unknown) => emit("folt_state_derived", payload));
  } catch {
    connected = false;
  }
}

function schedulePoll() {
  if (pollTimer !== null) window.clearInterval(pollTimer);
  if (document.visibilityState !== "visible") {
    pollTimer = null;
    return;
  }
  pollTimer = window.setInterval(() => pollHandler?.(), connected ? POLL_BACKSTOP_MS : POLL_IDLE_MS);
}

export function stop() {
  socket?.disconnect();
  socket = null;
  connected = false;
  document.removeEventListener("visibilitychange", onVisibility);
  window.removeEventListener("focus", onVisibility);
  if (pollTimer !== null) window.clearInterval(pollTimer);
  pollTimer = null;
}
