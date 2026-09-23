<script setup lang="ts">
// What this document leads to: the next document in its chain, created already filled in.
// Ready ones are buttons; ones already taken show what they made; the rest say when they open.
import { ref } from "vue";
import { useRouter } from "vue-router";

import { docPath, info } from "../../lib/doctypes";
import { money } from "../../lib/format";
import { runHandoff, type WriteResult } from "../../lib/store";
import type { Doc, Float, Handoff } from "../../lib/types";
import FieldRow from "../FieldRow.vue";
import FormAlert from "../FormAlert.vue";
import Icon from "../Icon.vue";
import StepSheet from "../StepSheet.vue";

const props = defineProps<{ doc: Doc; handoffs: Handoff[] }>();
const router = useRouter();

const busy = ref<string | null>(null);
const error = ref<WriteResult | null>(null);
const choosing = ref<{ handoff: Handoff; floats: Float[]; activity: string } | null>(null);
const chosenFloat = ref("");
const done = ref<{ label: string; name: string; doctype: string; lines: string[] } | null>(null);

async function run(handoff: Handoff, extra?: Record<string, string>) {
  busy.value = handoff.target;
  error.value = null;
  const res = await runHandoff(props.doc, handoff.target, extra);
  busy.value = null;
  if (!res.ok || !res.result) {
    error.value = res;
    return;
  }
  const result = res.result;
  if ("needs_float" in result && result.needs_float) {
    // One project, two funded floats: the register alone does not say which pays for this list.
    choosing.value = { handoff, floats: result.floats, activity: result.activity };
    chosenFloat.value = result.floats[0]?.name ?? "";
    return;
  }
  choosing.value = null;
  if ("name" in result) done.value = { label: result.label, name: result.name, doctype: result.doctype, lines: result.lines };
}

function openCreated() {
  if (!done.value) return;
  const to = docPath(done.value.doctype, done.value.name);
  done.value = null;
  router.push(to);
}
</script>

<template>
  <div class="handoffs">
    <div v-for="handoff in handoffs" :key="handoff.target + handoff.label" class="handoff">
      <div class="what">
        <span class="label">{{ handoff.label }}</span>
        <span class="desc">{{ handoff.description }}</span>
        <span v-if="handoff.existing.length" class="made">
          Raised:
          <RouterLink v-for="name in handoff.existing" :key="name" :to="docPath(handoff.target, name)" class="made-link">{{ name }}</RouterLink>
        </span>
        <span v-else-if="!handoff.ready" class="later">Opens once this reaches {{ handoff.ready_at }}.</span>
      </div>
      <button v-if="handoff.ready" type="button" class="btn btn-primary" :disabled="!!busy" @click="run(handoff)">
        <Icon name="plus" :size="16" /> {{ busy === handoff.target ? "Creating…" : `Create ${handoff.label.toLowerCase()}` }}
      </button>
    </div>
    <FormAlert v-if="error" :title="error.title" :html="error.error" />

    <StepSheet
      v-if="choosing"
      title="Which float pays for this list?"
      :subtitle="`${choosing.activity} has more than one funded float.`"
      :busy="!!busy"
      @close="choosing = null"
    >
      <FieldRow id="choose-float" label="Float" required>
        <select id="choose-float" v-model="chosenFloat">
          <option v-for="f in choosing.floats" :key="f.name" :value="f.name">
            {{ f.name }} — {{ f.employee_name }} — {{ money(f.paid_amount) }} paid ({{ f.workflow_state }})
          </option>
        </select>
      </FieldRow>
      <template #actions>
        <button type="button" class="btn btn-secondary" @click="choosing = null">Cancel</button>
        <button type="button" class="btn btn-primary go" :disabled="!chosenFloat || !!busy" @click="run(choosing.handoff, { employee_advance: chosenFloat })">
          Create the list
        </button>
      </template>
    </StepSheet>

    <StepSheet v-if="done" :title="`${done.label} ${done.name} created`" :subtitle="info(done.doctype).noun" @close="done = null">
      <ul v-if="done.lines.length" class="lines">
        <li v-for="line in done.lines" :key="line">{{ line }}</li>
      </ul>
      <p v-else class="lines-none">It has been filled in from {{ doc.name }}.</p>
      <template #actions>
        <button type="button" class="btn btn-secondary" @click="done = null">Stay here</button>
        <button type="button" class="btn btn-primary go" @click="openCreated">Open {{ done.name }}</button>
      </template>
    </StepSheet>
  </div>
</template>

<style scoped>
.handoffs {
  display: grid;
  gap: 0.75rem;
}
.handoff {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.85rem 1rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.what {
  display: grid;
  gap: 0.2rem;
  min-width: 0;
  flex: 1 1 16rem;
}
.label {
  color: var(--heading);
  font-weight: var(--weight-semibold);
}
.desc,
.later {
  color: var(--faint);
  font-size: var(--text-sm);
}
.made {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  color: var(--muted);
  font-size: var(--text-sm);
}
.made-link {
  font-weight: var(--weight-medium);
}
.lines {
  margin: 0;
  padding-left: 1.1rem;
  display: grid;
  gap: 0.35rem;
  font-size: var(--text-sm);
}
.lines-none {
  margin: 0;
  color: var(--muted);
}
</style>
