<script setup lang="ts">
// Label, control, hint, error -- the one place field spacing is decided. The label is always a
// real <label for>; the control is slotted and styled from here (and by .folt-input for controls
// that live inside a child component, which :slotted cannot reach -- the Gosolar LinkPicker bug).
defineProps<{ id: string; label: string; hint?: string | null; error?: string | null; required?: boolean }>();
</script>

<template>
  <div class="field" :class="{ bad: !!error }">
    <label :for="id">
      {{ label }}<span v-if="required" class="req" aria-hidden="true"> *</span>
      <span v-if="required" class="sr-only">(required)</span>
    </label>
    <slot />
    <p v-if="error" class="err" role="alert">{{ error }}</p>
    <p v-else-if="hint" class="hint">{{ hint }}</p>
  </div>
</template>

<style scoped>
.field {
  display: grid;
  align-content: start;
  gap: 0.35rem;
  min-width: 0;
}
label {
  color: var(--heading);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}
.req {
  color: var(--danger);
}
.hint,
.err {
  margin: 0;
  font-size: var(--text-xs);
  line-height: 1.45;
}
.hint {
  color: var(--faint);
}
.err {
  color: var(--danger);
}
/* A border and a focus ring: that is the whole difference between "here is your value" and
   "type your value here" (folt_portal.css). 16px text, or iOS zooms the viewport on focus. */
.field :slotted(input:not([type="checkbox"])),
.field :slotted(textarea),
.field :slotted(select) {
  width: 100%;
  min-height: var(--control-height);
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--fg);
  font-size: 16px;
  transition: border-color var(--dur-control) ease, box-shadow var(--dur-control) ease;
}
.field :slotted(textarea) {
  min-height: 6rem;
  resize: vertical;
  line-height: 1.5;
}
.field :slotted(input:focus),
.field :slotted(textarea:focus),
.field :slotted(select:focus) {
  outline: none;
  border-color: var(--brand);
  box-shadow: var(--focus-ring);
}
.field.bad :slotted(input),
.field.bad :slotted(textarea),
.field.bad :slotted(select) {
  border-color: var(--danger);
}
</style>
