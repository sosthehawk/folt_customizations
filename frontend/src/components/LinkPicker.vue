<script setup lang="ts">
// A picker over one step-form field's own option list (spa.options).
//
// It never calls frappe's search_link: that throws a bare 403 mid-form for a role that cannot read
// the target (the Head of Finance and FoLT Donor), whereas spa.options answers {allowed: false,
// reason} and the reason is shown beside the field. <datalist> was rejected: iOS Safari ignores it
// and Firefox will not show a label that differs from the value.
//
// Fixed from the Gosolar original: a value set from outside (an existing document's field) shows
// its label rather than an empty box, and choosing an option emits it whole so a form can prefill
// from `extra` (a returning participant's profile).
import { ref, watch } from "vue";

import { pick } from "../lib/store";
import type { PickerOption } from "../lib/types";

const props = defineProps<{
  id: string;
  doctype: string;
  fieldname: string;
  modelValue: string | null;
  display?: string | null;
  context?: Record<string, unknown>;
  placeholder?: string;
  invalid?: boolean;
  disabled?: boolean;
}>();
const emit = defineEmits<{ "update:modelValue": [string | null]; chosen: [PickerOption | null] }>();

const query = ref(props.display || props.modelValue || "");
const options = ref<PickerOption[]>([]);
const allowed = ref(true);
const reason = ref<string | null>(null);
const open = ref(false);
const loading = ref(false);
let seq = 0;
let loadedFor = "";

async function load() {
  const mine = ++seq;
  loading.value = true;
  const typed = props.modelValue && query.value === (props.display || props.modelValue) ? "" : query.value;
  const res = await pick(props.doctype, props.fieldname, typed, { ...(props.context ?? {}) });
  if (mine !== seq) return; // a later keystroke already superseded this answer
  allowed.value = res.allowed;
  reason.value = res.reason;
  options.value = res.options;
  loading.value = false;
  loadedFor = typed;
}

function choose(option: PickerOption) {
  query.value = option.label;
  open.value = false;
  emit("update:modelValue", option.value);
  emit("chosen", option);
}

function clear() {
  query.value = "";
  emit("update:modelValue", null);
  emit("chosen", null);
  void load();
}

function onInput() {
  open.value = true;
  // Typing after a choice means the choice no longer matches what is on screen.
  if (props.modelValue && query.value !== (props.display || props.modelValue)) emit("update:modelValue", null);
  void load();
}

function onFocus() {
  open.value = true;
  if (!options.value.length || loadedFor !== query.value) void load();
}

// The value can change from outside (a reset after save, a prefill); show its label, not a stale box.
watch(
  () => [props.modelValue, props.display] as const,
  ([value, label]) => {
    if (!value) {
      if (!open.value) query.value = "";
    } else if (!open.value) {
      query.value = label || value;
    }
  },
);
watch(() => JSON.stringify(props.context ?? {}), () => {
  if (open.value) void load();
  else options.value = [];
});
</script>

<template>
  <div class="picker">
    <div class="box" :class="{ bad: invalid, off: !allowed || disabled }">
      <input
        :id="id"
        v-model="query"
        class="folt-input"
        type="text"
        autocomplete="off"
        role="combobox"
        :aria-expanded="open"
        aria-autocomplete="list"
        :disabled="!allowed || disabled"
        :placeholder="allowed ? (placeholder ?? 'Start typing to search…') : 'Not available to you'"
        :aria-invalid="invalid ? 'true' : 'false'"
        @input="onInput"
        @focus="onFocus"
        @blur="open = false"
      />
      <button v-if="modelValue && allowed && !disabled" type="button" class="clear" aria-label="Clear" @mousedown.prevent="clear">×</button>
    </div>
    <p v-if="!allowed && reason" class="why">{{ reason }}</p>
    <ul v-if="open && allowed && options.length" class="list" role="listbox">
      <!-- mousedown, not click: blur fires first on click and the list is gone before it lands. -->
      <li v-for="option in options" :key="option.value">
        <button type="button" @mousedown.prevent="choose(option)">
          <span class="label">{{ option.label }}</span>
          <span v-if="option.label !== option.value" class="value">{{ option.value }}</span>
          <span v-if="option.hint" class="hint">{{ option.hint }}</span>
        </button>
      </li>
    </ul>
    <p v-if="open && allowed && !options.length && !loading" class="none">Nothing matches “{{ query }}”.</p>
  </div>
</template>

<style scoped>
.picker {
  position: relative;
  min-width: 0;
}
.box {
  position: relative;
}
.folt-input {
  width: 100%;
  min-height: var(--control-height);
  padding: 0.5rem 2.25rem 0.5rem 0.75rem;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--fg);
  font-size: 16px;
}
.folt-input:focus {
  outline: none;
  border-color: var(--brand);
  box-shadow: var(--focus-ring);
}
.bad .folt-input {
  border-color: var(--danger);
}
.off .folt-input {
  background: var(--surface-sunk);
  color: var(--muted);
  cursor: not-allowed;
}
.clear {
  position: absolute;
  top: 50%;
  right: 0.4rem;
  width: 28px;
  height: 28px;
  transform: translateY(-50%);
  border: 0;
  border-radius: 50%;
  background: none;
  color: var(--faint);
  font-size: 18px;
  cursor: pointer;
}
.clear:hover {
  background: var(--surface-sunk);
  color: var(--fg);
}
.why {
  margin: 0.3rem 0 0;
  color: var(--warn);
  font-size: var(--text-xs);
}
.list {
  position: absolute;
  z-index: 10;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  max-height: 16rem;
  margin: 0;
  padding: 4px;
  list-style: none;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface);
  box-shadow: var(--shadow-lift);
}
.list button {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0 0.5rem;
  width: 100%;
  min-height: 44px;
  padding: 0.45rem 0.65rem;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  text-align: left;
  cursor: pointer;
}
.list button:hover {
  background: var(--brand-tint);
}
.label {
  color: var(--heading);
  font-size: var(--text-sm);
}
.value {
  grid-column: 1;
  color: var(--faint);
  font-size: 12px;
}
.hint {
  grid-row: 1;
  grid-column: 2;
  color: var(--faint);
  font-size: 12px;
}
.none {
  margin: 0.3rem 0 0;
  color: var(--muted);
  font-size: var(--text-xs);
}
</style>
