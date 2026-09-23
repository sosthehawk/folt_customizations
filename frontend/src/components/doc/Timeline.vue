<script setup lang="ts">
// What has happened to the document, oldest first -- from the Workflow Comment rows
// document_guide reads, with a turn-down's two rows already collapsed into one entry.
import { computed } from "vue";

import { when } from "../../lib/format";
import type { TimelineEntry } from "../../lib/types";
import SafeHtml from "../SafeHtml.vue";

const props = defineProps<{ entries: TimelineEntry[] }>();

const VERB: Record<TimelineEntry["kind"], string> = {
  raised: "Raised",
  forward: "Moved to",
  turned_down: "Turned down to",
  derived: "Moved by the ledger to",
  finished: "Finished at",
  note: "Note",
};

// Built in script, not in the template: Vue's whitespace condensing ran the verb and the state
// together ("Moved toChecked") in the Gosolar journey.
const rows = computed(() =>
  props.entries.map((entry, i) => ({
    ...entry,
    key: `${entry.at}-${i}`,
    line: entry.kind === "raised" ? `Raised at ${entry.state}` : entry.kind === "note" ? entry.content ?? "" : `${VERB[entry.kind]} ${entry.state}`,
  })),
);
</script>

<template>
  <ol v-if="rows.length" class="timeline">
    <li v-for="row in rows" :key="row.key" class="event" :class="`is-${row.kind}`">
      <span class="dot" aria-hidden="true" />
      <div class="body">
        <p class="line">{{ row.line }}</p>
        <p class="meta">{{ row.by_name || row.by }} · {{ when(row.at) }}</p>
        <SafeHtml v-if="row.reason" :html="row.reason" tag="blockquote" class="reason" />
      </div>
    </li>
  </ol>
  <p v-else class="none">Nothing has happened to this document yet.</p>
</template>

<style scoped>
.timeline {
  position: relative;
  display: grid;
  gap: 1rem;
  margin: 0;
  padding: 0 0 0 1.25rem;
  list-style: none;
}
.timeline::before {
  content: "";
  position: absolute;
  top: 0.4rem;
  bottom: 0.4rem;
  left: 5px;
  width: 2px;
  background: var(--border);
}
.event {
  position: relative;
}
.dot {
  position: absolute;
  top: 0.35rem;
  left: calc(-1.25rem + 1px);
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--border-strong);
  box-shadow: 0 0 0 3px var(--surface);
}
.is-raised .dot { background: var(--brand); }
.is-forward .dot,
.is-finished .dot { background: var(--ok); }
.is-turned_down .dot { background: var(--danger); }
.is-derived .dot { background: var(--info); }
.line {
  margin: 0;
  color: var(--heading);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}
.meta {
  margin: 0.1rem 0 0;
  color: var(--faint);
  font-size: var(--text-xs);
}
.reason {
  margin: 0.45rem 0 0;
  padding: 0.5rem 0.75rem;
  border-left: 3px solid var(--danger);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  background: var(--danger-bg);
  color: var(--fg);
  font-size: var(--text-sm);
}
.none {
  margin: 0;
  color: var(--faint);
  font-size: var(--text-sm);
}
</style>
