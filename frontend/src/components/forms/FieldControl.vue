<script setup lang="ts">
// One control, chosen by the field's frappe type. The type, label, choices and link target all
// come from the server's field spec (read off the live meta), so nothing here is written per field.
import { computed } from "vue";

import type { FieldSpec, FieldValue, PickerOption } from "../../lib/types";
import LinkPicker from "../LinkPicker.vue";

const props = defineProps<{
  id: string;
  spec: FieldSpec;
  value: FieldValue;
  display?: string | null;
  doctype: string;
  /** The options key -- "location", or "participants.location" for a child column. */
  pickerField: string;
  context?: Record<string, unknown>;
  invalid?: boolean;
  disabled?: boolean;
}>();
const emit = defineEmits<{ "update:value": [FieldValue]; chosen: [PickerOption | null] }>();

const TEXTAREA = new Set(["Small Text", "Text", "Long Text", "Text Editor", "Code"]);
const NUMBER = new Set(["Int", "Float", "Currency", "Percent"]);

const kind = computed(() => {
  const t = props.spec.fieldtype;
  if (t === "Link") return "link";
  if (t === "Select") return "select";
  if (t === "Check") return "check";
  if (t === "Date") return "date";
  if (t === "Datetime") return "datetime";
  if (NUMBER.has(t)) return "number";
  if (TEXTAREA.has(t)) return "textarea";
  return "text";
});

function number(event: Event) {
  const raw = (event.target as HTMLInputElement).value;
  emit("update:value", raw === "" ? null : Number(raw));
}
function text(event: Event) {
  emit("update:value", (event.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement).value);
}
</script>

<template>
  <LinkPicker
    v-if="kind === 'link'"
    :id="id"
    :doctype="doctype"
    :fieldname="pickerField"
    :model-value="(value as string | null)"
    :display="display"
    :context="context"
    :invalid="invalid"
    :disabled="disabled"
    @update:model-value="(v) => emit('update:value', v)"
    @chosen="(o) => emit('chosen', o)"
  />
  <select v-else-if="kind === 'select'" :id="id" :value="value ?? ''" :disabled="disabled" :aria-invalid="invalid ? 'true' : 'false'" @change="text">
    <option v-for="choice in spec.choices ?? []" :key="choice" :value="choice">{{ choice || "—" }}</option>
  </select>
  <label v-else-if="kind === 'check'" class="check">
    <input
      :id="id"
      type="checkbox"
      :checked="!!Number(value)"
      :disabled="disabled"
      @change="emit('update:value', ($event.target as HTMLInputElement).checked ? 1 : 0)"
    />
    <span class="track" aria-hidden="true"><span class="knob" /></span>
    <span class="state">{{ Number(value) ? "Yes" : "No" }}</span>
  </label>
  <input
    v-else-if="kind === 'number'"
    :id="id"
    type="number"
    :step="spec.fieldtype === 'Int' ? 1 : 'any'"
    :inputmode="spec.fieldtype === 'Int' ? 'numeric' : 'decimal'"
    :value="value ?? ''"
    :disabled="disabled"
    :aria-invalid="invalid ? 'true' : 'false'"
    @input="number"
  />
  <input v-else-if="kind === 'date'" :id="id" type="date" :value="value ?? ''" :disabled="disabled" :aria-invalid="invalid ? 'true' : 'false'" @input="text" />
  <input v-else-if="kind === 'datetime'" :id="id" type="datetime-local" :value="value ?? ''" :disabled="disabled" @input="text" />
  <textarea v-else-if="kind === 'textarea'" :id="id" :value="(value as string) ?? ''" :disabled="disabled" :aria-invalid="invalid ? 'true' : 'false'" rows="3" @input="text" />
  <input v-else :id="id" type="text" :value="value ?? ''" :disabled="disabled" :aria-invalid="invalid ? 'true' : 'false'" @input="text" />
</template>

<style scoped>
.check {
  display: inline-flex;
  align-items: center;
  gap: 0.6rem;
  min-height: var(--control-height);
  cursor: pointer;
}
.check input {
  position: absolute;
  opacity: 0;
  width: 1px;
  height: 1px;
}
.track {
  position: relative;
  width: 42px;
  height: 24px;
  border-radius: var(--radius-pill);
  background: var(--border-strong);
  transition: background-color var(--dur-control) ease;
}
.knob {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #ffffff;
  box-shadow: 0 1px 2px rgb(0 0 0 / 0.25);
  transition: transform var(--dur-control) var(--ease-out);
}
.check input:checked + .track {
  background: var(--action);
}
.check input:checked + .track .knob {
  transform: translateX(18px);
}
.check input:focus-visible + .track {
  box-shadow: var(--focus-ring);
}
.check input:disabled + .track {
  opacity: 0.5;
}
.state {
  color: var(--muted);
  font-size: var(--text-sm);
}
</style>
