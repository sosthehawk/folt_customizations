<script setup lang="ts">
// Grouped by the STEP, not the doctype (folt_tasks._group's reasoning): "three floats to check" is
// one job done three times, while a list sorted by doctype hides that behind headings.
import { computed } from "vue";

import { info } from "../lib/doctypes";
import { roles } from "../lib/format";
import type { Bucket, TaskGroup, Tone } from "../lib/types";
import TaskRow from "./TaskRow.vue";

const props = defineProps<{ group: TaskGroup; bucket: Bucket; tones: Record<string, Tone> }>();

// A group whose lane is null is off the main path (turned down, a detour): its rows each say
// their own state rather than sharing one step heading.
const offPath = computed(() => props.group.lane === null);
</script>

<template>
  <section class="group card">
    <header class="head">
      <div class="what">
        <span class="kind">{{ info(group.doctype).plural }}</span>
        <h2 class="step">
          <template v-if="!offPath">{{ group.step_label }}</template>
          <template v-else>Off the main path</template>
        </h2>
      </div>
      <div class="where">
        <span v-if="!offPath && group.of" class="lane">Step {{ (group.lane ?? 0) + 1 }} of {{ group.of }}</span>
        <span v-if="bucket !== 'awaiting' && group.waiting_on.length" class="with">with {{ roles(group.waiting_on) }}</span>
        <span class="n">{{ group.rows.length }}</span>
      </div>
    </header>
    <TaskRow
      v-for="row in group.rows"
      :key="row.name"
      :row="row"
      :tone="tones[row.state ?? '']"
      :show-state="offPath || bucket !== 'awaiting'"
      :show-owner="bucket !== 'drafts'"
    />
  </section>
</template>

<style scoped>
.group {
  overflow: hidden;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 0.5rem 1rem;
  padding: 0.9rem 1rem 0.7rem;
}
.what {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.kind {
  color: var(--brand);
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.02em;
  text-transform: uppercase;
}
.step {
  font-size: var(--text-lg);
}
.where {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
  color: var(--faint);
  font-size: var(--text-xs);
}
.n {
  min-width: 1.6rem;
  padding: 0.1rem 0.45rem;
  border-radius: var(--radius-pill);
  background: var(--surface-sunk);
  color: var(--muted);
  font-weight: var(--weight-semibold);
  text-align: center;
  font-variant-numeric: tabular-nums;
}
</style>
