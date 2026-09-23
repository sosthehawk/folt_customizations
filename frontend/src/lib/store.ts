// The data every screen shares, fetched on demand and replaced -- never patched -- after a write.
//
// A hand-rolled `reactive` rather than Pinia: six screens and a dozen endpoints do not need a
// state library, and this project argues for every dependency it has.
//
// RELOAD, DON'T PATCH. Every write returns the whole document as the server now has it, and that
// replaces the copy here. Guessing the outcome client-side is how a screen starts disagreeing with
// the server: a save can move rows the client never touched (sync_quotation_scores rebuilds the
// committee grid; fetch_participants appends payees), and an action can open the next step for
// somebody else and change every count. Nothing here is keyed on `modified` either -- the ledger
// moves floats without a user action, so a document is refetched after every call, on focus, and
// on a realtime nudge.

import { reactive } from "vue";

import { call, callWithNotices, errorText, FrappeError, upload } from "./api";
import type {
  Bell,
  Bucket,
  Doc,
  DocumentList,
  HandoffResult,
  NewForm,
  Patch,
  PickerResult,
  Tasks,
  Workflow,
} from "./types";

const SPA = "folt_customizations.spa";
const TASKS = "folt_customizations.folt_customizations.page.folt_tasks.folt_tasks.my_tasks";
const NOTIFICATIONS = "folt_customizations.notifications";

type Slice<T> = { data: T; loading: boolean; loaded: boolean; error: string | null };

function slice<T>(initial: T): Slice<T> {
  return { data: initial, loading: false, loaded: false, error: null };
}

export type Toast = { id: number; html: string; tone: "info" | "ok" | "danger" };

export const store = reactive({
  tasks: {} as Record<Bucket, Slice<Tasks | null>>,
  /** Counts that can be trusted -- see loadTasks. Absent means "not known", never zero. */
  counts: {} as Partial<Record<Bucket, number>>,
  catalogue: slice<Workflow[]>([]),
  lists: {} as Record<string, Slice<DocumentList>>,
  docs: {} as Record<string, Slice<Doc | null>>,
  bell: slice<Bell>({ unread: 0, logs: [] }),
  toasts: [] as Toast[],
  /** Bumped by a realtime nudge or a poll. Each view watches it and reloads what IT shows --
   *  the shell does not need to know what is on screen. */
  pulse: 0,
});

export function pulse() {
  store.pulse++;
}

async function load<T>(target: Slice<T>, fetch: () => Promise<T>) {
  target.loading = true;
  target.error = null;
  try {
    target.data = await fetch();
    target.loaded = true;
  } catch (error) {
    target.error = errorText(error);
  } finally {
    target.loading = false;
  }
}

let toastId = 0;
export function toast(html: string, tone: Toast["tone"] = "info") {
  const id = ++toastId;
  store.toasts.push({ id, html, tone });
  window.setTimeout(() => dismiss(id), tone === "danger" ? 12_000 : 7_000);
}
export function dismiss(id: number) {
  const at = store.toasts.findIndex((t) => t.id === id);
  if (at !== -1) store.toasts.splice(at, 1);
}

// --- My Tasks ------------------------------------------------------------------------------------

export function tasksFor(bucket: Bucket): Slice<Tasks | null> {
  if (!store.tasks[bucket]) store.tasks[bucket] = slice<Tasks | null>(null);
  return store.tasks[bucket];
}

/** One bucket, and the counts it can vouch for.
 *
 *  THE UNTRUSTWORTHY-COUNTS RULE, carried over from folt_tasks.js. my_tasks computes the two
 *  backward-looking buckets (approved, archives) only when one of them is the bucket asked for, and
 *  reports a hard 0 for them otherwise -- absence, not emptiness. So only awaiting, drafts and the
 *  requested bucket are taken from a response; the others keep whatever was last known, and the
 *  tab shows no badge at all until something real arrives. A badge saying 0 would be a lie. */
export async function loadTasks(bucket: Bucket) {
  const target = tasksFor(bucket);
  await load(target, () => call<Tasks>(TASKS, { bucket }));
  const counts = target.data?.counts;
  if (!counts) return;
  const trusted: Bucket[] = ["awaiting", "drafts", bucket];
  for (const key of trusted) store.counts[key] = counts[key];
}

// --- workflows and lists -----------------------------------------------------------------------

export const loadCatalogue = () =>
  load(store.catalogue, async () => (await call<Workflow[]>(`${SPA}.catalogue`)) ?? []);

export function listKey(doctype: string, state: string | null, query: string) {
  return `${doctype}|${state ?? ""}|${query}`;
}

export function listFor(key: string): Slice<DocumentList> {
  if (!store.lists[key]) store.lists[key] = slice<DocumentList>({ rows: [], more: false });
  return store.lists[key];
}

export function loadList(doctype: string, state: string | null, query: string, start = 0) {
  const target = listFor(listKey(doctype, state, query));
  return load(target, async () => {
    const page = await call<DocumentList>(`${SPA}.documents`, { doctype, state, query, start, limit: 20 });
    // "Show more" appends; a fresh load (start 0) replaces.
    return start ? { rows: [...target.data.rows, ...page.rows], more: page.more } : page;
  });
}

// --- one document ------------------------------------------------------------------------------

export function docKey(doctype: string, name: string) {
  return `${doctype}::${name}`;
}

export function docFor(doctype: string, name: string): Slice<Doc | null> {
  const key = docKey(doctype, name);
  if (!store.docs[key]) store.docs[key] = slice<Doc | null>(null);
  return store.docs[key];
}

export function loadDoc(doctype: string, name: string) {
  return load(docFor(doctype, name), () => call<Doc>(`${SPA}.document`, { doctype, name }));
}

function install(doc: Doc) {
  const target = docFor(doc.doctype, doc.name);
  target.data = doc;
  target.loaded = true;
  target.error = null;
}

/** A write's outcome. `error` is the server's own message, HTML, for the form to show beside
 *  what the person was doing -- not a banner somewhere behind the sheet. */
export type WriteResult = { ok: boolean; error: string | null; title: string | null; stale: boolean };

function failure(error: unknown): WriteResult {
  if (error instanceof FrappeError) {
    return {
      ok: false,
      error: error.serverMessages.join("<br>") || error.message,
      title: error.title,
      stale: error.excType === "TimestampMismatchError",
    };
  }
  return { ok: false, error: errorText(error), title: null, stale: false };
}

function surface(notices: string[]) {
  for (const notice of notices) toast(notice, "info");
}

async function afterWrite() {
  // A write can put a document on somebody's list -- or take it off mine -- and change every
  // count; the task list and the bell are refreshed rather than adjusted.
  await Promise.all([loadTasks("awaiting"), loadBell()]);
}

export async function saveDoc(doc: Doc, values: Patch): Promise<WriteResult> {
  try {
    const { result, notices } = await callWithNotices<Doc>(`${SPA}.save`, {
      doctype: doc.doctype,
      name: doc.name,
      values,
      modified: doc.modified,
    });
    install(result);
    surface(notices);
    void afterWrite();
    return { ok: true, error: null, title: null, stale: false };
  } catch (error) {
    return failure(error);
  }
}

/** One call: edits (if any, and only where the actor holds the step), reason, transition. */
export async function actOn(doc: Doc, action: string, reason?: string, values?: Patch): Promise<WriteResult> {
  try {
    const { result, notices } = await callWithNotices<Doc>(`${SPA}.act`, {
      doctype: doc.doctype,
      name: doc.name,
      action,
      reason: reason ?? null,
      values: values && Object.keys(values).length ? values : null,
      modified: doc.modified,
    });
    install(result);
    surface(notices);
    void afterWrite();
    return { ok: true, error: null, title: null, stale: false };
  } catch (error) {
    return failure(error);
  }
}

export async function attachTo(doc: Doc, fieldname: string, file: File): Promise<WriteResult> {
  try {
    const { result, notices } = await upload<Doc>(
      `${SPA}.attach`,
      { doctype: doc.doctype, name: doc.name, fieldname, modified: doc.modified },
      file,
    );
    install(result);
    surface(notices);
    return { ok: true, error: null, title: null, stale: false };
  } catch (error) {
    return failure(error);
  }
}

export async function newForm(doctype: string): Promise<NewForm> {
  return call<NewForm>(`${SPA}.new_form`, { doctype });
}

export async function createDoc(doctype: string, values: Patch): Promise<WriteResult & { name?: string }> {
  try {
    const { result, notices } = await callWithNotices<{ name: string; document: Doc }>(`${SPA}.create`, {
      doctype,
      values,
    });
    install(result.document);
    surface(notices);
    void afterWrite();
    return { ok: true, error: null, title: null, stale: false, name: result.name };
  } catch (error) {
    return failure(error);
  }
}

export async function runHandoff(
  doc: Doc,
  target: string,
  extra?: Record<string, string>,
): Promise<(WriteResult & { result?: HandoffResult })> {
  try {
    const { result, notices } = await callWithNotices<HandoffResult>(`${SPA}.handoff`, {
      doctype: doc.doctype,
      name: doc.name,
      target,
      extra: extra ?? null,
    });
    surface(notices);
    if (!("needs_float" in result && result.needs_float)) {
      void loadDoc(doc.doctype, doc.name);
      void afterWrite();
    }
    return { ok: true, error: null, title: null, stale: false, result };
  } catch (error) {
    return failure(error);
  }
}

/** A whitelisted FoLT method that saves the document itself (autofill_roster, fetch_participants).
 *  They keep their own rules; this just runs them and reloads. */
export async function runTool(doc: Doc, method: string, args: Record<string, unknown>): Promise<WriteResult & { result?: unknown }> {
  try {
    const { result, notices } = await callWithNotices<unknown>(method, args);
    surface(notices);
    await loadDoc(doc.doctype, doc.name);
    return { ok: true, error: null, title: null, stale: false, result };
  } catch (error) {
    return failure(error);
  }
}

/** Choices for one step-form field. Never throws: a permission gap comes back as
 *  {allowed: false, reason} so the form says why beside the field. */
export async function pick(
  doctype: string,
  fieldname: string,
  query: string,
  context: Record<string, unknown>,
): Promise<PickerResult> {
  try {
    return (
      (await call<PickerResult>(`${SPA}.options`, { doctype, fieldname, query, context })) ?? {
        allowed: false,
        reason: "No answer from the server.",
        options: [],
      }
    );
  } catch (error) {
    return { allowed: false, reason: errorText(error), options: [] };
  }
}

// --- the bell ----------------------------------------------------------------------------------

export const loadBell = () =>
  load(store.bell, async () => (await call<Bell>(`${NOTIFICATIONS}.bell`, { limit: 30 })) ?? { unread: 0, logs: [] });

export async function markRead(name?: string) {
  await call(`${NOTIFICATIONS}.mark_notifications_read`, name ? { name } : {});
  await loadBell();
}

export async function clearRead(name?: string) {
  await call(`${NOTIFICATIONS}.clear_read_notifications`, name ? { name } : {});
  await loadBell();
}
