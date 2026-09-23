<script setup lang="ts">
// A child table a step may edit: attendees on a register, payees on a reimbursement list, the
// committee's members and their own scores.
//
// Rows are KEYED BY ROW NAME, never by index -- a stateful picker in a row keyed by index shows
// the wrong label after a middle row is removed (the Gosolar ItemRows defect), and the server
// addresses rows by name anyway. Edits go into the draft as a diff; an existing row is removed
// only by an explicit toggle, never by being left out, and new rows are drafts until saved.
import { computed, reactive } from "vue";

import type { Draft } from "../../lib/draft";
import { money } from "../../lib/format";
import type { FieldSpec, FieldValue, PickerOption, TableSpec } from "../../lib/types";
import Icon from "../Icon.vue";
import FieldControl from "./FieldControl.vue";

const props = defineProps<{ table: TableSpec; draft: Draft; doctype: string; docName: string; currency?: string | null; disabled?: boolean }>();

const t = computed(() => props.draft.table(props.table.fieldname));
const removed = computed(() => new Set(t.value.remove));
const labels = reactive<Record<string, string | null>>({});
const warnings = reactive<Record<string, string>>({});

// The profile a known participant brings with them (spa._PREFILL), as on the Desk form.
const PREFILL = ["participant_name", "mobile_number", "id_number", "location", "gender", "is_pwd", "photo_consent"];

function read(row: string, column: string, isNew: boolean): FieldValue {
  if (isNew) return t.value.add.find((r) => r.key === row)?.values[column] ?? null;
  return props.draft.cell(props.table.fieldname, row, column);
}

function write(row: string, column: string, value: FieldValue, isNew: boolean) {
  if (isNew) {
    const target = t.value.add.find((r) => r.key === row);
    if (target) target.values[column] = value;
  } else {
    props.draft.setCell(props.table.fieldname, row, column, value);
  }
}

function chosen(row: string, column: string, option: PickerOption | null, isNew: boolean) {
  labels[`${row}:${column}`] = option?.label ?? null;
  if (column !== "participant" || !option) return;
  // The same person twice on one register is refused by the server; say so at the pick instead.
  const elsewhere = [
    ...props.table.rows.filter((r) => r.name !== row && read(r.name, "participant", false) === option.value),
    ...t.value.add.filter((r) => r.key !== row && r.values.participant === option.value),
  ];
  if (elsewhere.length) {
    warnings[row] = `${option.label} is already on this list.`;
    write(row, column, null, isNew);
    return;
  }
  delete warnings[row];
  for (const field of PREFILL) {
    const v = option.extra?.[field];
    if (v !== undefined && v !== null && v !== "" && props.table.columns.some((c) => c.fieldname === field)) write(row, field, v, isNew);
  }
}

function label(spec: FieldSpec, row: { name: string; display: Record<string, string | null> }) {
  return labels[`${row.name}:${spec.fieldname}`] ?? row.display[spec.fieldname] ?? null;
}

function shownValue(spec: FieldSpec, value: FieldValue, display: string | null) {
  if (value === null || value === undefined || value === "") return "—";
  if (spec.fieldtype === "Check") return Number(value) ? "Yes" : "No";
  if (spec.fieldtype === "Currency") return money(Number(value), props.currency);
  return display || String(value);
}

function add() {
  props.draft.addRow(props.table.fieldname, { ...props.table.new_row });
}

const count = computed(() => props.table.rows.length - removed.value.size + t.value.add.length);
</script>

<template>
  <section class="rows">
    <header class="head">
      <h3>{{ table.label }} <span class="n">{{ count }}</span></h3>
      <p v-if="table.own_rows" class="note">You can fill in your own rows only. Each member scores their own.</p>
    </header>

    <ol class="list">
      <li v-for="row in table.rows" :key="row.name" class="row" :class="{ gone: removed.has(row.name), mine: row.editable }">
        <div class="row-top">
          <span class="idx">{{ row.idx }}</span>
          <span class="shown">
            <span v-for="spec in table.show" :key="spec.fieldname" class="show-item">
              <span class="show-label">{{ spec.label }}</span>
              <span class="show-value">{{ shownValue(spec, row.values[spec.fieldname], row.display[spec.fieldname]) }}</span>
            </span>
          </span>
          <button
            v-if="table.remove && !disabled"
            type="button"
            class="remove"
            :aria-label="removed.has(row.name) ? 'Keep this row' : 'Remove this row'"
            :title="removed.has(row.name) ? 'Keep this row' : 'Remove this row'"
            @click="draft.toggleRemove(table.fieldname, row.name)"
          >
            <Icon :name="removed.has(row.name) ? 'undo' : 'x'" :size="16" />
          </button>
        </div>
        <p v-if="removed.has(row.name)" class="gone-note">Will be removed when you save.</p>
        <div v-else class="cells">
          <div v-for="spec in table.columns" :key="spec.fieldname" class="cell" :class="{ check: spec.fieldtype === 'Check' }">
            <label :for="`${table.fieldname}-${row.name}-${spec.fieldname}`" class="cell-label">
              {{ spec.label }}<span v-if="spec.reqd" class="req"> *</span>
            </label>
            <FieldControl
              v-if="row.editable && !disabled"
              :id="`${table.fieldname}-${row.name}-${spec.fieldname}`"
              :spec="spec"
              :value="read(row.name, spec.fieldname, false)"
              :display="label(spec, row)"
              :doctype="doctype"
              :picker-field="`${table.fieldname}.${spec.fieldname}`"
              :context="{ name: docName }"
              @update:value="(v) => write(row.name, spec.fieldname, v, false)"
              @chosen="(o) => chosen(row.name, spec.fieldname, o, false)"
            />
            <span v-else :id="`${table.fieldname}-${row.name}-${spec.fieldname}`" class="static">
              {{ shownValue(spec, row.values[spec.fieldname], row.display[spec.fieldname]) }}
            </span>
          </div>
          <p v-if="warnings[row.name]" class="warn">{{ warnings[row.name] }}</p>
        </div>
      </li>

      <li v-for="row in t.add" :key="row.key" class="row new">
        <div class="row-top">
          <span class="idx new-tag">New</span>
          <span class="shown" />
          <button type="button" class="remove" aria-label="Discard this new row" @click="draft.dropNew(table.fieldname, row.key)">
            <Icon name="x" :size="16" />
          </button>
        </div>
        <div class="cells">
          <div v-for="spec in table.columns" :key="spec.fieldname" class="cell" :class="{ check: spec.fieldtype === 'Check' }">
            <label :for="`${table.fieldname}-${row.key}-${spec.fieldname}`" class="cell-label">
              {{ spec.label }}<span v-if="spec.reqd" class="req"> *</span>
            </label>
            <FieldControl
              :id="`${table.fieldname}-${row.key}-${spec.fieldname}`"
              :spec="spec"
              :value="row.values[spec.fieldname] ?? null"
              :display="labels[`${row.key}:${spec.fieldname}`]"
              :doctype="doctype"
              :picker-field="`${table.fieldname}.${spec.fieldname}`"
              :context="{ name: docName }"
              @update:value="(v) => write(row.key, spec.fieldname, v, true)"
              @chosen="(o) => chosen(row.key, spec.fieldname, o, true)"
            />
          </div>
          <p v-if="warnings[row.key]" class="warn">{{ warnings[row.key] }}</p>
        </div>
      </li>
    </ol>

    <p v-if="!table.rows.length && !t.add.length" class="empty">No rows yet.</p>
    <button v-if="table.add && !disabled" type="button" class="btn btn-secondary add" @click="add">
      <Icon name="plus" :size="16" /> Add a row
    </button>
  </section>
</template>

<style scoped>
.rows {
  display: grid;
  gap: 0.75rem;
}
.head h3 {
  font-size: var(--text-base);
}
.n {
  margin-left: 0.3rem;
  color: var(--faint);
  font-weight: var(--weight-regular);
}
.note {
  margin: 0.2rem 0 0;
  color: var(--faint);
  font-size: var(--text-xs);
}
.list {
  display: grid;
  gap: 0.6rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.row {
  display: grid;
  gap: 0.6rem;
  padding: 0.75rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.row.mine {
  border-color: color-mix(in srgb, var(--brand) 35%, var(--border));
}
.row.new {
  border-style: dashed;
  border-color: var(--brand);
  background: var(--brand-tint);
}
.row.gone {
  opacity: 0.6;
}
.row-top {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
}
.idx {
  flex: none;
  min-width: 1.6rem;
  padding: 0.05rem 0.4rem;
  border-radius: var(--radius-pill);
  background: var(--surface-sunk);
  color: var(--faint);
  font-size: 12px;
  text-align: center;
}
.new-tag {
  background: var(--brand);
  color: var(--action-fg);
  font-weight: 700;
}
.shown {
  display: flex;
  flex-wrap: wrap;
  gap: 0.2rem 1rem;
  flex: 1;
  min-width: 0;
}
.show-item {
  display: grid;
}
.show-label {
  color: var(--faint);
  font-size: 12px;
}
.show-value {
  color: var(--heading);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  overflow-wrap: anywhere;
}
.remove {
  display: grid;
  place-items: center;
  flex: none;
  width: 34px;
  height: 34px;
  margin-left: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--muted);
  cursor: pointer;
}
.remove:hover {
  border-color: var(--danger);
  color: var(--danger);
}
.gone-note {
  margin: 0;
  color: var(--danger);
  font-size: var(--text-xs);
}
.cells {
  display: grid;
  align-items: start;
  gap: 0.6rem 0.9rem;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 12rem), 1fr));
}
.cell {
  display: grid;
  align-content: start;
  gap: 0.25rem;
  min-width: 0;
}
.cell-label {
  color: var(--muted);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
}
.req {
  color: var(--danger);
}
.static {
  min-height: 1.5rem;
  color: var(--fg);
  font-size: var(--text-sm);
}
.cell :deep(input:not([type="checkbox"])),
.cell :deep(select),
.cell :deep(textarea) {
  width: 100%;
  min-height: 40px;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--fg);
  font-size: 16px;
}
.cell :deep(textarea) {
  min-height: 4rem;
}
.cell :deep(input:focus),
.cell :deep(select:focus),
.cell :deep(textarea:focus) {
  outline: none;
  border-color: var(--brand);
  box-shadow: var(--focus-ring);
}
.warn {
  grid-column: 1 / -1;
  margin: 0;
  color: var(--warn);
  font-size: var(--text-xs);
}
.empty {
  margin: 0;
  color: var(--faint);
  font-size: var(--text-sm);
}
.add {
  justify-self: start;
}
</style>
