<script setup lang="ts">
// The confirmation before an action that cannot be taken back, and the reason a turn-down must
// carry. Both are asked BEFORE the request: spa.act refuses a turn-down with no reason before it
// writes anything, and this form refuses it before it sends anything.
import { computed, ref } from "vue";

import type { Action } from "../../lib/types";
import FieldRow from "../FieldRow.vue";
import FormAlert from "../FormAlert.vue";
import StepSheet from "../StepSheet.vue";

const props = defineProps<{ action: Action; docName: string; saving: boolean; busy: boolean; error: string | null; errorTitle: string | null; stale: boolean }>();
const emit = defineEmits<{ close: []; confirm: [string | undefined]; reload: [] }>();

const reason = ref("");
const touched = ref(false);
const missing = computed(() => props.action.needs_reason && !reason.value.trim());

function submit() {
  touched.value = true;
  if (missing.value) return;
  emit("confirm", props.action.needs_reason ? reason.value.trim() : undefined);
}
</script>

<template>
  <StepSheet :title="`${action.label} ${docName}`" :subtitle="action.needs_reason ? `It goes to ${action.next_state}. Say what has to change — the person it goes back to acts on this.` : `It moves to ${action.next_state}.`" :busy="busy" @close="emit('close')">
    <FieldRow v-if="action.needs_reason" id="act-reason" label="Reason" required :error="touched && missing ? 'Give a reason — somebody has to act on it.' : null">
      <textarea id="act-reason" v-model="reason" rows="4" placeholder="What is wrong, and what should be done about it" />
    </FieldRow>
    <p v-else-if="action.submits" class="warn">
      This submits the document. After this, only the fields the next steps take can change.
    </p>
    <p v-if="saving" class="note">Your unsaved changes are saved first, in the same step.</p>
    <FormAlert :title="errorTitle" :html="error" :stale="stale" @reload="emit('reload')" />
    <template #actions>
      <button type="button" class="btn btn-secondary" :disabled="busy" @click="emit('close')">Cancel</button>
      <button type="button" class="btn go" :class="action.kind === 'turn_down' ? 'btn-danger' : 'btn-primary'" :disabled="busy" @click="submit">
        {{ busy ? "Working…" : saving ? `Save & ${action.label}` : action.label }}
      </button>
    </template>
  </StepSheet>
</template>

<style scoped>
.warn,
.note {
  margin: 0;
  font-size: var(--text-sm);
}
.warn {
  color: var(--warn);
}
.note {
  color: var(--muted);
}
</style>
