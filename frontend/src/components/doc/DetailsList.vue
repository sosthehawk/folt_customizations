<script setup lang="ts">
// The read-only details, in the sections step_forms.SUMMARY declares. Link values show the
// target's title where the reader may see it; the server leaves out fields above their permlevel.
import { money, day } from "../../lib/format";
import type { FieldSpec, FieldValue, SummarySection } from "../../lib/types";

import { computed } from "vue";

const props = defineProps<{ sections: SummarySection[] }>();

// A section whose every field is empty is a heading over nothing; leave it out.
const visible = computed(() =>
  props.sections.filter(
    (s) => s.table || (s.fields ?? []).some((f) => f.value !== null && f.value !== undefined && f.value !== ""),
  ),
);

function show(field: FieldSpec, value: FieldValue, display: string | null | undefined, currency: string | null) {
  if (value === null || value === undefined || value === "") return null;
  if (field.fieldtype === "Check") return Number(value) ? "Yes" : "No";
  if (field.fieldtype === "Currency") return money(Number(value), field.currency ?? currency);
  if (field.fieldtype === "Date") return day(String(value));
  return display || String(value);
}
</script>

<template>
  <div class="details">
    <section v-for="section in visible" :key="section.label" class="section">
      <h3>{{ section.label }}</h3>
      <dl v-if="section.fields" class="fields">
        <template v-for="field in section.fields" :key="field.fieldname">
          <div v-if="show(field, field.value ?? null, field.display, section.currency)" class="pair">
            <dt>{{ field.label }}</dt>
            <dd>{{ show(field, field.value ?? null, field.display, section.currency) }}</dd>
          </div>
        </template>
      </dl>
      <div v-else-if="section.table" class="table-wrap">
        <table v-if="section.table.rows.length">
          <thead>
            <tr><th v-for="col in section.table.columns" :key="col.fieldname" scope="col">{{ col.label }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in section.table.rows" :key="row.name">
              <td v-for="col in section.table.columns" :key="col.fieldname" :data-label="col.label">
                {{ show(col, row.values[col.fieldname], row.display[col.fieldname], section.currency) ?? "—" }}
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="none">None.</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.details {
  display: grid;
  gap: 1.25rem;
}
.section h3 {
  margin-bottom: 0.6rem;
  color: var(--muted);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.fields {
  display: grid;
  gap: 0.75rem 1.25rem;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 13rem), 1fr));
  margin: 0;
}
.pair {
  display: grid;
  gap: 0.1rem;
  min-width: 0;
}
dt {
  color: var(--faint);
  font-size: var(--text-xs);
}
dd {
  margin: 0;
  color: var(--fg);
  overflow-wrap: anywhere;
  white-space: pre-line;
}
.table-wrap {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-sm);
}
th {
  padding: 0.45rem 0.6rem;
  border-bottom: 1px solid var(--border-strong);
  color: var(--faint);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  text-align: left;
  white-space: nowrap;
}
td {
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
.none {
  margin: 0;
  color: var(--faint);
  font-size: var(--text-sm);
}
/* At phone width a table becomes a stack of labelled cells rather than a sideways scroll. */
@media (max-width: 40rem) {
  thead {
    display: none;
  }
  table,
  tbody,
  tr,
  td {
    display: block;
  }
  tr {
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--border);
  }
  td {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    padding: 0.2rem 0;
    border: 0;
  }
  td::before {
    content: attr(data-label);
    color: var(--faint);
    font-size: var(--text-xs);
  }
}
</style>
