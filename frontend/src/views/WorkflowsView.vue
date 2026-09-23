<script setup lang="ts">
// Every chain this person can see, with where its documents are sitting. The lanes are the
// server's derived step plan (workflow_shape), so a workflow edited next month redraws itself.
import { computed, onMounted, watch } from "vue";

import EmptyState from "../components/EmptyState.vue";
import SkeletonList from "../components/SkeletonList.vue";
import StateChip from "../components/StateChip.vue";
import { info, listPath } from "../lib/doctypes";
import { loadCatalogue, store } from "../lib/store";
import type { Workflow } from "../lib/types";

const CHAINS = [
  { key: "activity", title: "Activities and floats", hint: "Requisition → float → attendance → reimbursement → retirement" },
  { key: "procurement", title: "Procurement", hint: "Bid → committee or waiver → purchase order" },
  { key: "payroll", title: "Payroll", hint: "Salary slips through approval" },
] as const;

const grouped = computed(() =>
  CHAINS.map((chain) => ({
    ...chain,
    flows: store.catalogue.data.filter((wf) => info(wf.doctype).chain === chain.key),
  })).filter((chain) => chain.flows.length),
);

function laneCount(wf: Workflow, states: string[]) {
  return states.reduce((sum, state) => sum + (wf.counts[state] ?? 0), 0);
}

function total(wf: Workflow) {
  return Object.values(wf.counts).reduce((sum, n) => sum + (n ?? 0), 0);
}

onMounted(() => void loadCatalogue());
watch(() => store.pulse, () => void loadCatalogue());
</script>

<template>
  <div class="page">
    <header>
      <h1>Workflows</h1>
      <p class="lede">Every approval chain you can see, and where its documents are sitting.</p>
    </header>

    <SkeletonList v-if="!store.catalogue.loaded" :rows="4" height="9rem" />
    <p v-else-if="store.catalogue.error" class="card error" role="alert">{{ store.catalogue.error }}</p>
    <EmptyState v-else-if="!grouped.length" icon="flows" title="No workflows" hint="Your roles do not give you any FoLT approval chain to read." />

    <section v-for="chain in grouped" :key="chain.key" class="chain">
      <div class="chain-head">
        <h2>{{ chain.title }}</h2>
        <p>{{ chain.hint }}</p>
      </div>
      <div class="grid">
        <article v-for="wf in chain.flows" :key="wf.doctype" class="flow card">
          <RouterLink :to="listPath(wf.doctype)" class="flow-link">
            <span class="flow-head">
              <span class="flow-name">{{ info(wf.doctype).plural }}</span>
              <span class="flow-total">{{ total(wf) }}</span>
            </span>
            <span v-if="wf.chain" class="flow-chain">Step {{ wf.chain.step }} of {{ wf.chain.of }} · {{ wf.chain.title }}</span>
          </RouterLink>
          <ol v-if="wf.lanes.length" class="lanes">
            <li v-for="lane in wf.lanes" :key="lane.rank" class="lane">
              <RouterLink :to="{ path: listPath(wf.doctype), query: { state: lane.states[0] } }" class="lane-link">
                <span class="lane-label">{{ lane.label }}</span>
                <span class="lane-n" :class="{ zero: !laneCount(wf, [...lane.states, ...lane.optional]) }">
                  {{ laneCount(wf, [...lane.states, ...lane.optional]) }}
                </span>
              </RouterLink>
            </li>
          </ol>
          <div v-if="Object.keys(wf.off_path).length" class="off">
            <RouterLink
              v-for="(_, state) in wf.off_path"
              :key="state"
              :to="{ path: listPath(wf.doctype), query: { state } }"
              class="off-link"
            >
              <StateChip :state="`${state} · ${wf.counts[state] ?? 0}`" :tone="wf.tones[state]" size="sm" />
            </RouterLink>
          </div>
          <RouterLink v-if="wf.can_create" :to="`/new/${info(wf.doctype).slug}`" class="btn btn-secondary new">
            New {{ info(wf.doctype).noun.toLowerCase() }}
          </RouterLink>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  display: grid;
  gap: 1.75rem;
}
h1 {
  font-size: var(--text-3xl);
}
.lede {
  margin: 0.3rem 0 0;
  color: var(--muted);
}
.chain {
  display: grid;
  gap: 0.75rem;
}
.chain-head h2 {
  font-size: var(--text-xl);
}
.chain-head p {
  margin: 0.15rem 0 0;
  color: var(--faint);
  font-size: var(--text-sm);
}
.grid {
  display: grid;
  gap: 1rem;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 21rem), 1fr));
}
.flow {
  display: grid;
  align-content: start;
  gap: 0.85rem;
  padding: 1.1rem;
}
.flow-link {
  display: grid;
  gap: 0.2rem;
  color: inherit;
  text-decoration: none;
}
.flow-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 0.5rem;
}
.flow-name {
  color: var(--heading);
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
}
.flow-link:hover .flow-name {
  color: var(--brand);
}
.flow-total {
  color: var(--faint);
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
}
.flow-chain {
  color: var(--faint);
  font-size: var(--text-xs);
}
.lanes {
  display: grid;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
  counter-reset: lane;
}
.lane-link {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.5rem;
  min-height: 36px;
  padding: 0.3rem 0.6rem;
  border-radius: var(--radius-sm);
  color: var(--fg);
  font-size: var(--text-sm);
  text-decoration: none;
}
.lane-link:hover {
  background: var(--brand-tint);
}
.lane-label {
  display: flex;
  gap: 0.35rem;
}
.lane-label::before {
  counter-increment: lane;
  content: counter(lane) ".";
  flex: none;
  width: 1.2rem;
  color: var(--faint);
  font-variant-numeric: tabular-nums;
}
.lane-n {
  min-width: 1.6rem;
  padding: 0 0.4rem;
  border-radius: var(--radius-pill);
  background: var(--brand-tint-strong);
  color: var(--brand);
  font-size: 12px;
  font-weight: 700;
  line-height: 1.4rem;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.lane-n.zero {
  background: none;
  color: var(--faint);
  font-weight: var(--weight-regular);
}
.off {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}
.off-link {
  text-decoration: none;
}
.new {
  justify-self: start;
}
.error {
  padding: 1rem;
  color: var(--danger);
}
</style>
