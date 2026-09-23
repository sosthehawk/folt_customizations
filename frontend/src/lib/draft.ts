// What somebody has typed but not saved, kept as a DIFF against the server's values.
//
// The patch sent to spa.save / spa.act is exactly this diff: the scalar fields that changed, and
// per child table the rows updated (by row name), added and removed. Nothing the person did not
// touch is ever sent, which is the whole point of owning a patch rather than a document -- a
// colleague's committee score, a payee the server appended, a field the controller derives are
// never re-sent and so can never be overwritten or duplicated by this client.

import { computed, reactive } from "vue";

import type { FieldValue, Form, Patch, RowOps, TableSpec } from "./types";

export type NewRow = { key: string; values: Record<string, FieldValue> };

type TableDraft = {
  update: Record<string, Record<string, FieldValue>>;
  add: NewRow[];
  remove: string[];
};

let seq = 0;

export function useDraft(form: () => Form | null) {
  const state = reactive({
    fields: {} as Record<string, FieldValue>,
    tables: {} as Record<string, TableDraft>,
  });

  function base(fieldname: string): FieldValue {
    const f = form();
    const spec = f?.fields.find((x) => x.fieldname === fieldname) ?? f?.virtual.find((x) => x.fieldname === fieldname);
    return spec?.value ?? null;
  }

  function table(fieldname: string): TableDraft {
    if (!state.tables[fieldname]) state.tables[fieldname] = { update: {}, add: [], remove: [] };
    return state.tables[fieldname];
  }

  function spec(fieldname: string): TableSpec | undefined {
    return form()?.tables.find((t) => t.fieldname === fieldname);
  }

  /** The value on screen: the edit if there is one, the server's otherwise. */
  function value(fieldname: string): FieldValue {
    return fieldname in state.fields ? state.fields[fieldname] : base(fieldname);
  }

  function set(fieldname: string, next: FieldValue) {
    if (same(next, base(fieldname))) delete state.fields[fieldname];
    else state.fields[fieldname] = next;
  }

  function cell(tableName: string, row: string, column: string): FieldValue {
    const edits = state.tables[tableName]?.update[row];
    if (edits && column in edits) return edits[column];
    return spec(tableName)?.rows.find((r) => r.name === row)?.values[column] ?? null;
  }

  function setCell(tableName: string, row: string, column: string, next: FieldValue) {
    const t = table(tableName);
    const original = spec(tableName)?.rows.find((r) => r.name === row)?.values[column] ?? null;
    const edits = (t.update[row] ??= {});
    if (same(next, original)) delete edits[column];
    else edits[column] = next;
    if (!Object.keys(edits).length) delete t.update[row];
  }

  function addRow(tableName: string, values: Record<string, FieldValue> = {}): NewRow {
    const row: NewRow = { key: `new-${++seq}`, values: { ...values } };
    table(tableName).add.push(row);
    return row;
  }

  function dropNew(tableName: string, key: string) {
    const t = table(tableName);
    t.add = t.add.filter((r) => r.key !== key);
  }

  function toggleRemove(tableName: string, row: string) {
    const t = table(tableName);
    t.remove = t.remove.includes(row) ? t.remove.filter((r) => r !== row) : [...t.remove, row];
  }

  function reset() {
    state.fields = {};
    state.tables = {};
  }

  const dirty = computed(
    () =>
      Object.keys(state.fields).length > 0 ||
      Object.values(state.tables).some((t) => Object.keys(t.update).length || t.add.length || t.remove.length),
  );

  function patch(): Patch {
    const out: Patch = { ...state.fields };
    for (const [name, t] of Object.entries(state.tables)) {
      const ops: RowOps = {};
      const update = Object.entries(t.update).map(([row, values]) => ({ name: row, ...values }));
      if (update.length) ops.update = update;
      if (t.add.length) ops.add = t.add.map((r) => ({ ...r.values }));
      if (t.remove.length) ops.remove = [...t.remove];
      if (Object.keys(ops).length) out[name] = ops;
    }
    return out;
  }

  return { state, value, set, cell, setCell, addRow, dropNew, toggleRemove, table, reset, dirty, patch };
}

export type Draft = ReturnType<typeof useDraft>;

/** Loose equality for form values: "" and null are both empty; "1" and 1 are the same Check. */
export function same(a: FieldValue, b: FieldValue): boolean {
  const empty = (v: FieldValue) => v === null || v === undefined || v === "";
  if (empty(a) && empty(b)) return true;
  return String(a) === String(b);
}

/** Whether a show_if condition holds against the current values. */
export function shown(showIf: Record<string, FieldValue> | undefined, read: (f: string) => FieldValue): boolean {
  if (!showIf) return true;
  return Object.entries(showIf).every(([field, expected]) => same(read(field) ?? 0, expected));
}

/** Required fields with nothing in them, by fieldname -> message. Mirrors the server's `reqd`. */
export function missing(form: Form, read: (f: string) => FieldValue): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of [...form.fields, ...form.virtual]) {
    if (!field.reqd || field.fieldtype === "Attach" || field.fieldtype === "Check") continue;
    if (!shown(form.show_if[field.fieldname], read)) continue;
    const v = read(field.fieldname);
    if (v === null || v === undefined || String(v).trim() === "") out[field.fieldname] = `${field.label} is needed.`;
  }
  return out;
}
