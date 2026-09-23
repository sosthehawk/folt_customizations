<script setup lang="ts">
// The action bar: the forward move large and first, turn-downs secondary and red-outlined, and
// the manual float corrections tucked under a disclosure because the ledger normally makes them.
//
// The actions are the SERVER's (spa.actions_for): frappe's own self-approval predicate has already
// been applied, so nothing here decides who may act. A dirty form turns the forward action into
// "Save & <action>" -- one request, because apply_workflow reloads from the database and would
// otherwise drop the edits (see spa.act).
import { computed, ref } from "vue";

import type { Action } from "../../lib/types";

const props = defineProps<{ actions: Action[]; dirty: boolean; canSave: boolean; busy: boolean }>();
const emit = defineEmits<{ act: [Action]; save: [] }>();

const forward = computed(() => props.actions.filter((a) => a.kind === "forward"));
const turnDowns = computed(() => props.actions.filter((a) => a.kind === "turn_down"));
const corrections = computed(() => props.actions.filter((a) => a.kind === "correction"));
const showCorrections = ref(false);

function label(action: Action) {
  return props.dirty && props.canSave ? `Save & ${action.label}` : action.label;
}
</script>

<template>
  <div class="bar">
    <div class="main">
      <button
        v-for="action in forward"
        :key="action.action"
        type="button"
        class="btn"
        :class="action.primary ? 'btn-primary big' : 'btn-secondary'"
        :disabled="busy || action.blocks.length > 0"
        :title="action.blocks.length ? `Needs: ${action.blocks.join(', ')}` : undefined"
        @click="emit('act', action)"
      >
        {{ label(action) }}
      </button>
      <button v-if="dirty && canSave" type="button" class="btn btn-secondary" :disabled="busy" @click="emit('save')">Save only</button>
      <button
        v-for="action in turnDowns"
        :key="action.action"
        type="button"
        class="btn btn-danger"
        :disabled="busy"
        @click="emit('act', action)"
      >
        {{ action.label }}…
      </button>
    </div>
    <p v-for="action in forward.filter((a) => a.blocks.length)" :key="`b-${action.action}`" class="blocked">
      {{ action.label }} needs {{ action.blocks.join(", ") }} first.
    </p>
    <div v-if="corrections.length" class="corrections">
      <button type="button" class="btn btn-quiet" :aria-expanded="showCorrections" @click="showCorrections = !showCorrections">
        Correct the state{{ showCorrections ? "" : "…" }}
      </button>
      <template v-if="showCorrections">
        <p class="note">The ledger normally moves a float through these — a Payment Entry makes it Disbursed, its retirement makes it Accounted. Use these only to correct it.</p>
        <div class="main">
          <button v-for="action in corrections" :key="action.action" type="button" class="btn btn-secondary" :disabled="busy" @click="emit('act', action)">
            {{ action.label }}
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.bar {
  display: grid;
  gap: 0.6rem;
}
.main {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
}
.big {
  min-height: 48px;
  padding: 0 1.4rem;
  font-size: var(--text-base);
}
@media (max-width: 40rem) {
  .main .btn {
    flex: 1 1 100%;
  }
}
.blocked {
  margin: 0;
  color: var(--danger);
  font-size: var(--text-sm);
}
.corrections {
  display: grid;
  gap: 0.5rem;
  justify-items: start;
  padding-top: 0.4rem;
  border-top: 1px dashed var(--border);
}
.note {
  margin: 0;
  color: var(--faint);
  font-size: var(--text-xs);
}
</style>
