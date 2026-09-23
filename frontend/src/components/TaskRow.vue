<script setup lang="ts">
// One document in a queue: a link, because it is one -- Tab reaches it, Enter opens it, and
// cmd-click opens it in a new tab, none of which a click handler on a div gives you.
import { computed } from "vue";

import { docPath, info } from "../lib/doctypes";
import { age, isStale, money } from "../lib/format";
import type { TaskRow, Tone } from "../lib/types";
import StateChip from "./StateChip.vue";

const props = defineProps<{ row: TaskRow; tone?: Tone; showState?: boolean; showOwner?: boolean }>();

const stale = computed(() => isStale(props.row.age_days));
</script>

<template>
  <RouterLink :to="docPath(row.doctype, row.name)" class="row">
    <span class="main">
      <span class="title">{{ row.title }}</span>
      <span class="meta">
        <span v-if="row.title !== row.name" class="name">{{ row.name }}</span>
        <template v-if="showOwner && row.owner_name"><template v-if="row.title !== row.name"> · </template>{{ row.owner_name }}</template>
        <template v-if="!showState && info(row.doctype).noun"> · {{ info(row.doctype).noun }}</template>
      </span>
    </span>
    <StateChip v-if="showState" :state="row.state" :tone="tone" size="sm" class="state" />
    <span v-if="row.amount" class="amount">{{ money(row.amount) }}</span>
    <span class="age" :class="{ stale }" :title="stale ? 'Waiting a week or more' : undefined">{{ age(row.modified) }}</span>
  </RouterLink>
</template>

<style scoped>
.row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto auto;
  align-items: center;
  gap: 0.75rem;
  min-height: 60px;
  padding: 0.7rem 1rem;
  border-top: 1px solid var(--border);
  color: inherit;
  text-decoration: none;
  transition: background-color var(--dur-control) ease;
}
.row:hover {
  background: var(--brand-tint);
}
.row:focus-visible {
  position: relative;
  z-index: 1;
}
.main {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.title {
  color: var(--heading);
  font-weight: var(--weight-medium);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.meta {
  color: var(--faint);
  font-size: var(--text-xs);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.amount {
  font-variant-numeric: tabular-nums;
  font-size: var(--text-sm);
  color: var(--muted);
}
.age {
  min-width: 2.5rem;
  color: var(--faint);
  font-size: var(--text-xs);
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.age.stale {
  color: var(--warn);
  font-weight: var(--weight-semibold);
}
@media (max-width: 40rem) {
  .row {
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .amount,
  .state {
    grid-column: 1;
    justify-self: start;
  }
  .age {
    grid-row: 1;
    grid-column: 2;
  }
}
</style>
