<script setup lang="ts">
// My Tasks: what is waiting on me, my drafts, what I moved on, and what is finished.
//
// folt_tasks.my_tasks is the Desk page's own endpoint, used unchanged, so /desk/folt-tasks and this
// screen cannot disagree about whose turn anything is. The bucket is in the query string, so the
// tab survives a reload and a shared link opens on the same one.
import { computed, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import EmptyState from "../components/EmptyState.vue";
import SkeletonList from "../components/SkeletonList.vue";
import TaskGroup from "../components/TaskGroup.vue";
import { boot } from "../lib/boot";
import { loadCatalogue, loadTasks, store, tasksFor } from "../lib/store";
import type { Bucket, Tone } from "../lib/types";

const TABS: { key: Bucket; label: string; empty: string; hint: string }[] = [
  { key: "awaiting", label: "Waiting on me", empty: "Nothing is waiting on you", hint: "When a document reaches a step you hold, it appears here — oldest first." },
  { key: "drafts", label: "My drafts", empty: "No drafts", hint: "Documents you have started but not sent on, including any sent back to you." },
  { key: "approved", label: "Moving on", empty: "Nothing in progress", hint: "Documents you raised or acted on that are submitted and still moving — a float being spent, a list being paid." },
  { key: "archives", label: "Finished", empty: "Nothing finished yet", hint: "Documents you raised or acted on that have reached the end of their chain, or were turned down." },
];

const route = useRoute();
const router = useRouter();
const bucket = computed<Bucket>(() => {
  const asked = route.query.tab as Bucket;
  return TABS.some((t) => t.key === asked) ? asked : "awaiting";
});
const current = computed(() => tasksFor(bucket.value));
const tab = computed(() => TABS.find((t) => t.key === bucket.value)!);

const tones = computed(() => {
  const all: Record<string, Tone> = {};
  for (const wf of store.catalogue.data) Object.assign(all, wf.tones);
  return all;
});

const firstName = computed(() => boot.full_name.split(" ")[0]);

function choose(key: Bucket) {
  router.replace({ query: key === "awaiting" ? {} : { tab: key } });
}

onMounted(() => {
  void loadTasks(bucket.value);
  if (!store.catalogue.loaded) void loadCatalogue();
});
watch(bucket, (key) => void loadTasks(key));
watch(() => store.pulse, () => void loadTasks(bucket.value));
</script>

<template>
  <div class="tasks">
    <header class="hero">
      <p class="hello">Hello, {{ firstName }}</p>
      <h1>My Tasks</h1>
      <p class="lede" v-if="store.counts.awaiting">
        {{ store.counts.awaiting }} {{ store.counts.awaiting === 1 ? "document is" : "documents are" }} waiting on you.
      </p>
      <p class="lede" v-else-if="store.counts.awaiting === 0">You are all caught up.</p>
    </header>

    <div class="tabs" role="tablist" aria-label="Task lists">
      <button
        v-for="t in TABS"
        :key="t.key"
        type="button"
        role="tab"
        class="tab"
        :class="{ on: t.key === bucket }"
        :aria-selected="t.key === bucket"
        @click="choose(t.key)"
      >
        {{ t.label }}
        <!-- A count the server did not compute is absent, not zero: see store.loadTasks. -->
        <span v-if="store.counts[t.key] !== undefined" class="count">{{ store.counts[t.key] }}</span>
      </button>
    </div>

    <SkeletonList v-if="!current.loaded && current.loading" :rows="3" height="8rem" />
    <p v-else-if="current.error" class="error card" role="alert">{{ current.error }}</p>
    <EmptyState
      v-else-if="current.loaded && !current.data?.groups.length"
      icon="check"
      :title="tab.empty"
      :hint="tab.hint"
    />
    <div v-else class="groups">
      <TaskGroup v-for="group in current.data?.groups ?? []" :key="group.key" :group="group" :bucket="bucket" :tones="tones" />
    </div>
  </div>
</template>

<style scoped>
.tasks {
  display: grid;
  gap: 1.25rem;
}
.hero {
  display: grid;
  gap: 0.2rem;
}
.hello {
  margin: 0;
  color: var(--brand);
  font-weight: var(--weight-medium);
}
h1 {
  font-size: var(--text-3xl);
}
.lede {
  margin: 0.2rem 0 0;
  color: var(--muted);
}
.tabs {
  display: flex;
  gap: 0.25rem;
  padding: 4px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface-sunk);
  overflow-x: auto;
  scrollbar-width: none;
}
.tab {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  flex: 1 0 auto;
  justify-content: center;
  min-height: 40px;
  padding: 0 0.9rem;
  border: 0;
  border-radius: calc(var(--radius-md) - 4px);
  background: none;
  color: var(--muted);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  white-space: nowrap;
  cursor: pointer;
  transition: background-color var(--dur-control) ease, color var(--dur-control) ease;
}
.tab.on {
  background: var(--surface);
  color: var(--heading);
  box-shadow: var(--shadow-card);
}
.count {
  min-width: 1.4rem;
  padding: 0 0.35rem;
  border-radius: var(--radius-pill);
  background: var(--brand-tint-strong);
  color: var(--brand);
  font-size: 12px;
  font-weight: 700;
  line-height: 1.4rem;
  font-variant-numeric: tabular-nums;
}
.groups {
  display: grid;
  gap: 1rem;
}
.error {
  padding: 1rem;
  color: var(--danger);
}
</style>
