<script setup lang="ts">
// One workflow's documents: filter by state, search by name or title, newest first.
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import EmptyState from "../components/EmptyState.vue";
import Icon from "../components/Icon.vue";
import SkeletonList from "../components/SkeletonList.vue";
import StateChip from "../components/StateChip.vue";
import TaskRow from "../components/TaskRow.vue";
import { fromSlug } from "../lib/doctypes";
import { listFor, listKey, loadCatalogue, loadList, store } from "../lib/store";

const props = defineProps<{ slug: string }>();
const route = useRoute();
const router = useRouter();

const meta = computed(() => fromSlug(props.slug));
const doctype = computed(() => meta.value?.doctype ?? "");
const workflow = computed(() => store.catalogue.data.find((wf) => wf.doctype === doctype.value));
const state = computed(() => (route.query.state as string) || null);
const query = ref((route.query.q as string) || "");
const settled = ref(query.value);

const list = computed(() => listFor(listKey(doctype.value, state.value, settled.value)));

/** Every state worth filtering by, in the order the chain runs, with its count. */
const states = computed(() => {
  const wf = workflow.value;
  if (!wf) return [];
  const ordered = wf.lanes.length
    ? [...wf.lanes.flatMap((lane) => [...lane.states, ...lane.optional]), ...Object.keys(wf.off_path)]
    : Object.keys(wf.counts);
  return [...new Set(ordered)].map((s) => ({ state: s, count: wf.counts[s] ?? 0, tone: wf.tones[s] }));
});

let timer: number | undefined;
watch(query, (value) => {
  window.clearTimeout(timer);
  timer = window.setTimeout(() => {
    settled.value = value.trim();
    router.replace({ query: { ...route.query, q: settled.value || undefined } });
  }, 250);
});

function setState(next: string | null) {
  router.replace({ query: { ...route.query, state: next || undefined } });
}

function reload() {
  if (doctype.value) void loadList(doctype.value, state.value, settled.value);
}

onMounted(() => {
  if (!store.catalogue.loaded) void loadCatalogue();
  reload();
});
watch([doctype, state, settled], reload);
watch(() => store.pulse, reload);
</script>

<template>
  <div class="page">
    <template v-if="meta">
      <header class="head">
        <div>
          <RouterLink to="/workflows" class="back"><Icon name="back" :size="16" /> Workflows</RouterLink>
          <h1>{{ meta.plural }}</h1>
        </div>
        <RouterLink v-if="workflow?.can_create" :to="`/new/${meta.slug}`" class="btn btn-primary">
          <Icon name="plus" :size="18" /> New {{ meta.noun.toLowerCase() }}
        </RouterLink>
      </header>

      <div class="tools">
        <label class="search">
          <Icon name="search" :size="18" />
          <span class="sr-only">Search {{ meta.plural.toLowerCase() }}</span>
          <input v-model="query" type="search" :placeholder="`Search by name or title`" autocomplete="off" />
        </label>
        <div class="filters" role="group" aria-label="Filter by state">
          <button type="button" class="filter" :class="{ on: !state }" @click="setState(null)">All</button>
          <button
            v-for="s in states"
            :key="s.state"
            type="button"
            class="filter"
            :class="{ on: state === s.state }"
            :aria-pressed="state === s.state"
            @click="setState(s.state)"
          >
            <StateChip :state="s.state" :tone="s.tone" size="sm" />
            <span class="n">{{ s.count }}</span>
          </button>
        </div>
      </div>

      <SkeletonList v-if="!list.loaded && list.loading" :rows="5" height="3.75rem" />
      <p v-else-if="list.error" class="card error" role="alert">{{ list.error }}</p>
      <EmptyState
        v-else-if="list.loaded && !list.data.rows.length"
        icon="search"
        :title="settled || state ? 'Nothing matches' : `No ${meta.plural.toLowerCase()} yet`"
        :hint="settled || state ? 'Try another state or a shorter search.' : undefined"
      />
      <div v-else class="card rows">
        <TaskRow
          v-for="row in list.data.rows"
          :key="row.name"
          :row="row"
          :tone="workflow?.tones[row.state ?? '']"
          show-state
          show-owner
        />
      </div>
      <button
        v-if="list.data.more"
        type="button"
        class="btn btn-secondary more"
        :disabled="list.loading"
        @click="loadList(doctype, state, settled, list.data.rows.length)"
      >
        Show more
      </button>
    </template>
    <EmptyState v-else icon="alert" title="Not a FoLT workflow" hint="This link does not point at a document list /folt serves." />
  </div>
</template>

<style scoped>
.page {
  display: grid;
  gap: 1.25rem;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 1rem;
}
.back {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  margin-bottom: 0.3rem;
  color: var(--muted);
  font-size: var(--text-sm);
  text-decoration: none;
}
h1 {
  font-size: var(--text-3xl);
}
.tools {
  display: grid;
  gap: 0.75rem;
}
.search {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-height: var(--control-height);
  padding: 0 0.85rem;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--faint);
}
.search:focus-within {
  border-color: var(--brand);
  box-shadow: var(--focus-ring);
}
.search input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: 0;
  background: none;
  color: var(--fg);
  font-size: 16px;
}
.filters {
  display: flex;
  gap: 0.4rem;
  overflow-x: auto;
  padding-bottom: 2px;
  scrollbar-width: thin;
}
.filter {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  flex: none;
  min-height: 36px;
  padding: 0 0.65rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-pill);
  background: var(--surface);
  color: var(--muted);
  font-size: var(--text-sm);
  cursor: pointer;
}
.filter.on {
  border-color: var(--brand);
  box-shadow: 0 0 0 1px var(--brand);
  color: var(--heading);
}
.n {
  color: var(--faint);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.rows {
  overflow: hidden;
}
.rows :deep(.row:first-child) {
  border-top: 0;
}
.more {
  justify-self: center;
}
.error {
  padding: 1rem;
  color: var(--danger);
}
</style>
