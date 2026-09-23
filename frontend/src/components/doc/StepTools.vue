<script setup lang="ts">
// The helpers a register or a reimbursement list step offers, each a FoLT method that already
// exists and keeps its own rules:
//
//   autofill_roster      copy a roster from an earlier session       (APL controller; saves itself)
//   import_sheet         read the attached signed sheet (OCR/native) (APL controller; writes nothing)
//   reconcile            tick who signed, mark the rest absent       (client-side, into the draft)
//   print_sheet          the printable roster for signing            (FoLT Attendance Sheet format)
//   fetch_participants   pull eligible attendees onto the list       (PRL controller; saves itself)
//   mark_all_paid        every pending payee paid and signed         (client-side, into the draft)
//
// The two that save refuse to run over unsaved edits, as the Desk buttons do: they reload the
// document afterwards, and a reload would silently discard what somebody had typed.
import { computed, ref } from "vue";

import type { Draft } from "../../lib/draft";
import { call } from "../../lib/api";
import { runTool, type WriteResult } from "../../lib/store";
import type { Doc, FieldValue } from "../../lib/types";
import FieldRow from "../FieldRow.vue";
import FormAlert from "../FormAlert.vue";
import Icon from "../Icon.vue";
import StepSheet from "../StepSheet.vue";

const props = defineProps<{ doc: Doc; draft: Draft; tools: string[] }>();

const APL = "folt_customizations.folt_customizations.doctype.activity_participant_list.activity_participant_list";
const PRL = "folt_customizations.folt_customizations.doctype.participant_reimbursement_list.participant_reimbursement_list";

const busy = ref<string | null>(null);
const error = ref<WriteResult | null>(null);
const note = ref<string | null>(null);
const sheet = ref<"autofill" | "reconcile" | null>(null);

const participants = computed(() => props.doc.form.tables.find((t) => t.fieldname === "participants"));

function guardClean(): boolean {
  if (!props.draft.dirty.value) return true;
  error.value = { ok: false, title: "Save first", error: "Save or discard your changes first — this reloads the document when it is done.", stale: false };
  return false;
}

// --- autofill a roster ---------------------------------------------------------------------------
type Source = { name: string; session_date: string; venue: string | null; total_attendees: number; docstatus: number };
const sources = ref<Source[]>([]);
const fromRegister = ref("");
const attendedOnly = ref(true);

async function openAutofill() {
  if (!guardClean()) return;
  error.value = null;
  busy.value = "autofill";
  try {
    sources.value = (await call<Source[]>(`${APL}.get_roster_sources`, { register: props.doc.name })) ?? [];
  } finally {
    busy.value = null;
  }
  if (!sources.value.length) {
    note.value = "No earlier register on this activity to copy a roster from.";
    return;
  }
  fromRegister.value = sources.value[0].name;
  sheet.value = "autofill";
}

async function autofill() {
  busy.value = "autofill";
  const res = await runTool(props.doc, `${APL}.autofill_roster`, {
    register: props.doc.name,
    from_register: fromRegister.value,
    attended_only: attendedOnly.value ? 1 : 0,
  });
  busy.value = null;
  if (!res.ok) {
    error.value = res;
    return;
  }
  sheet.value = null;
  const r = res.result as { added: number; skipped_existing: number; truncated?: boolean };
  note.value = `${r.added} ${r.added === 1 ? "person" : "people"} added as a roster — nobody is ticked present yet.${r.skipped_existing ? ` ${r.skipped_existing} already on the register.` : ""}`;
}

// --- import from the signed sheet ----------------------------------------------------------------
type SheetRow = Record<string, FieldValue> & { _flags?: string[] };

async function importSheet() {
  error.value = null;
  note.value = null;
  busy.value = "import";
  try {
    const res = await call<{ rows: SheetRow[]; flagged: number; warnings: string[]; skipped_existing: number; source: string }>(
      `${APL}.read_attendance_sheet`,
      { register: props.doc.name },
    );
    const columns = new Set(participants.value?.columns.map((c) => c.fieldname) ?? []);
    let added = 0;
    const flagged: string[] = [];
    for (const row of res.rows ?? []) {
      const values: Record<string, FieldValue> = {};
      for (const [key, value] of Object.entries(row)) {
        if (key.startsWith("_") || value === null || value === "" || !columns.has(key)) continue;
        values[key] = value as FieldValue;
      }
      props.draft.addRow("participants", { acknowledgement: "", ...values });
      added++;
      if (row._flags?.length) flagged.push(`${row.participant_name ?? "A row"}: ${row._flags.join("; ")}`);
    }
    note.value =
      `${added} row(s) read from the sheet (${res.source === "ocr" ? "scanned" : "typed"}) and added below, unsaved — check them, then save.` +
      (res.skipped_existing ? ` ${res.skipped_existing} already on the register.` : "") +
      (flagged.length ? ` Check: ${flagged.join(" · ")}` : "") +
      (res.warnings?.length ? ` ${res.warnings.join(" ")}` : "");
  } catch (e) {
    error.value = { ok: false, title: "Could not read the sheet", error: e instanceof Error ? e.message : String(e), stale: false };
  } finally {
    busy.value = null;
  }
}

// --- reconcile -----------------------------------------------------------------------------------
const signed = ref<Set<string>>(new Set());
const mark = ref<"Signature" | "Thumbprint" | "None">("Signature");
const restAbsent = ref(true);

function openReconcile() {
  error.value = null;
  signed.value = new Set(
    (participants.value?.rows ?? [])
      .filter((r) => Number(props.draft.cell("participants", r.name, "attended")))
      .map((r) => r.name),
  );
  sheet.value = "reconcile";
}

function toggle(name: string) {
  const next = new Set(signed.value);
  if (next.has(name)) next.delete(name);
  else next.add(name);
  signed.value = next;
}

// Keyed by row NAME, never idx -- the Desk's reconcile dialog made the same choice for the same
// reason: the rows can be re-ordered between opening the sheet and applying it.
function applyReconcile() {
  for (const row of participants.value?.rows ?? []) {
    if (signed.value.has(row.name)) {
      props.draft.setCell("participants", row.name, "attended", 1);
      props.draft.setCell("participants", row.name, "acknowledgement", mark.value);
    } else if (restAbsent.value) {
      props.draft.setCell("participants", row.name, "attended", 0);
      props.draft.setCell("participants", row.name, "acknowledgement", "None");
    }
  }
  sheet.value = null;
  note.value = `${signed.value.size} marked present. Save to keep it.`;
}

// --- reimbursement list --------------------------------------------------------------------------
async function fetchParticipants() {
  if (!guardClean()) return;
  error.value = null;
  busy.value = "fetch";
  const register = (props.draft.value("attendance_reference") as string) || undefined;
  const res = await runTool(props.doc, `${PRL}.fetch_participants`, { reimbursement_list: props.doc.name, register });
  busy.value = null;
  if (!res.ok) {
    error.value = res;
    return;
  }
  const r = res.result as { added: number; skipped_ineligible: number };
  note.value = `${r.added} payee(s) pulled from the register${r.skipped_ineligible ? `; ${r.skipped_ineligible} skipped as not eligible` : ""}.`;
}

function markAllPaid() {
  let n = 0;
  for (const row of participants.value?.rows ?? []) {
    if (!row.editable) continue;
    const status = props.draft.cell("participants", row.name, "payment_status");
    if (status && status !== "Pending") continue;
    props.draft.setCell("participants", row.name, "payment_status", "Paid");
    props.draft.setCell("participants", row.name, "acknowledgement", "Signature");
    n++;
  }
  note.value = n ? `${n} pending payee(s) marked paid and signed. Adjust any that failed, then save.` : "No payees are pending.";
}

const printUrl = computed(
  () => `/printview?doctype=${encodeURIComponent(props.doc.doctype)}&name=${encodeURIComponent(props.doc.name)}&format=${encodeURIComponent("FoLT Attendance Sheet")}`,
);
</script>

<template>
  <div v-if="tools.length" class="tools">
    <div class="buttons">
      <button v-if="tools.includes('autofill_roster')" type="button" class="btn btn-secondary" :disabled="!!busy" @click="openAutofill">
        <Icon name="undo" :size="16" /> Copy a roster
      </button>
      <button v-if="tools.includes('import_sheet')" type="button" class="btn btn-secondary" :disabled="!!busy" @click="importSheet">
        <Icon name="file" :size="16" /> {{ busy === "import" ? "Reading the sheet…" : "Read the signed sheet" }}
      </button>
      <button v-if="tools.includes('reconcile') && participants?.rows.length" type="button" class="btn btn-secondary" :disabled="!!busy" @click="openReconcile">
        <Icon name="check" :size="16" /> Tick who signed
      </button>
      <a v-if="tools.includes('print_sheet')" :href="printUrl" target="_blank" rel="noopener" class="btn btn-secondary">
        <Icon name="external" :size="16" /> Print the sheet
      </a>
      <button v-if="tools.includes('fetch_participants')" type="button" class="btn btn-secondary" :disabled="!!busy" @click="fetchParticipants">
        <Icon name="plus" :size="16" /> {{ busy === "fetch" ? "Pulling payees…" : "Pull payees from the register" }}
      </button>
      <button v-if="tools.includes('mark_all_paid')" type="button" class="btn btn-secondary" :disabled="!!busy" @click="markAllPaid">
        <Icon name="check" :size="16" /> Mark all pending paid &amp; signed
      </button>
    </div>
    <p v-if="note" class="note" role="status">{{ note }}</p>
    <FormAlert v-if="error" :title="error.title" :html="error.error" />

    <StepSheet v-if="sheet === 'autofill'" title="Copy a roster" subtitle="Everyone on an earlier session, added unticked, for this session's sheet." :busy="busy === 'autofill'" @close="sheet = null">
      <FieldRow id="roster-from" label="From register" required>
        <select id="roster-from" v-model="fromRegister">
          <option v-for="s in sources" :key="s.name" :value="s.name">{{ s.session_date }} · {{ s.total_attendees }} attendees{{ s.venue ? ` · ${s.venue}` : "" }} · {{ s.name }}</option>
        </select>
      </FieldRow>
      <label class="inline"><input v-model="attendedOnly" type="checkbox" /> Only people who attended that session</label>
      <template #actions>
        <button type="button" class="btn btn-secondary" @click="sheet = null">Cancel</button>
        <button type="button" class="btn btn-primary go" :disabled="busy === 'autofill'" @click="autofill">Copy the roster</button>
      </template>
    </StepSheet>

    <StepSheet v-if="sheet === 'reconcile'" title="Tick who signed" subtitle="Everyone ticked is marked present with the mark below." @close="sheet = null">
      <ul class="tick-list">
        <li v-for="row in participants?.rows ?? []" :key="row.name">
          <label class="inline">
            <input type="checkbox" :checked="signed.has(row.name)" @change="toggle(row.name)" />
            {{ row.values.participant_name }} <span class="faint">{{ row.values.mobile_number }}</span>
          </label>
        </li>
      </ul>
      <FieldRow id="reconcile-mark" label="Their mark">
        <select id="reconcile-mark" v-model="mark">
          <option>Signature</option>
          <option>Thumbprint</option>
          <option>None</option>
        </select>
      </FieldRow>
      <label class="inline"><input v-model="restAbsent" type="checkbox" /> Mark everyone else absent</label>
      <template #actions>
        <button type="button" class="btn btn-secondary" @click="sheet = null">Cancel</button>
        <button type="button" class="btn btn-primary go" @click="applyReconcile">Apply</button>
      </template>
    </StepSheet>
  </div>
</template>

<style scoped>
.tools {
  display: grid;
  gap: 0.6rem;
}
.buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.note {
  margin: 0;
  padding: 0.6rem 0.8rem;
  border-radius: var(--radius-md);
  background: var(--brand-tint);
  color: var(--fg);
  font-size: var(--text-sm);
}
.inline {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 40px;
  font-size: var(--text-sm);
  cursor: pointer;
}
.inline input {
  width: 20px;
  height: 20px;
  accent-color: var(--action);
}
.tick-list {
  display: grid;
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 45vh;
  overflow-y: auto;
}
.faint {
  color: var(--faint);
}
</style>
