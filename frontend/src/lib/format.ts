// Turning server values into the words somebody would actually say.
//
// Frappe hands back naive datetimes as "2026-09-16 13:50:54.797092" -- no zone, no T. Safari
// refuses that outright and returns NaN, which is how a date silently becomes "Invalid Date" on
// exactly one browser. Every parse in the app goes through `parse` below for that reason.
//
// There is deliberately no state -> colour map here. Which tone a workflow state reads as is
// decided on the server (spa.state_tone) from the workflow's own shape, because keying it on the
// words the states use goes wrong in FoLT immediately: Draft is styled "Danger" in
// fixtures/workflow_state.json, and Checked, Disbursed, Accounted, Verified and Settled share no
// keyword with anything.

/** Frappe's "YYYY-MM-DD HH:mm:ss[.ffffff]" -> Date. Null for anything unparseable. */
export function parse(value: string | null | undefined): Date | null {
  if (!value) return null;
  const at = new Date(value.replace(" ", "T"));
  return Number.isNaN(at.getTime()) ? null : at;
}

/** Whole days since `value`. */
export function daysSince(value: string | null | undefined): number {
  const at = parse(value);
  if (!at) return 0;
  return Math.max(0, Math.floor((Date.now() - at.getTime()) / 86_400_000));
}

/** "today", "3d", "2w" -- how long something has been sitting. */
export function age(value: string | null | undefined): string {
  const days = daysSince(value);
  if (days === 0) return "today";
  if (days === 1) return "1d";
  if (days < 14) return `${days}d`;
  return `${Math.floor(days / 7)}w`;
}

/** Same threshold as folt_tasks.js, so the Desk and /folt agree on what counts as waiting too long. */
export const STALE_DAYS = 7;

export function isStale(days: number | null | undefined): boolean {
  return (days ?? 0) >= STALE_DAYS;
}

/** "16 Sep, 10:12" -- enough to place an event without a full timestamp. */
export function when(value: string | null | undefined): string {
  const at = parse(value);
  if (!at) return "";
  return at.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** "16 Sep 2026" -- for a date field, which has no useful time. */
export function day(value: string | null | undefined): string {
  const at = parse(value);
  if (!at) return "";
  return at.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** A money amount in its own currency. Falls back to a plain number for an unknown code. */
export function money(amount: number | null | undefined, currency?: string | null): string {
  if (amount === null || amount === undefined || Number.isNaN(Number(amount))) return "";
  try {
    return new Intl.NumberFormat(undefined, {
      style: currency ? "currency" : "decimal",
      currency: currency || undefined,
      maximumFractionDigits: 2,
    }).format(Number(amount));
  } catch {
    return Number(amount).toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
}

/** A login reduced to something readable, when the server could not name the person. */
export function shortUser(login: string | null | undefined): string {
  if (!login) return "";
  return login.split("@")[0].replace(/[._]/g, " ");
}

/** "Head of Finance" or "Head of Finance or Finance Officer". */
export function roles(list: string[] | null | undefined): string {
  const items = (list ?? []).filter(Boolean);
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;
}
