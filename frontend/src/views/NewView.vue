<script setup lang="ts">
// Starting something. Only two FoLT documents are started from nothing -- an activity requisition
// and a waiver request; every other one is created by the step before it, already filled in, and
// this page says so rather than offering a blank form that would skip the chain.
import { computed, onMounted } from "vue";

import EmptyState from "../components/EmptyState.vue";
import Icon from "../components/Icon.vue";
import { info } from "../lib/doctypes";
import { loadCatalogue, store } from "../lib/store";

const DESCRIPTIONS: Record<string, string> = {
  "Activity Requisition": "Plan an activity and its budget. Once approved, the float request and the attendance register are raised from it.",
  "Derogation Waiver Request": "Make the case for buying from one supplier without competing it. Once authorised, the purchase order is raised from it.",
};

const startable = computed(() => store.catalogue.data.filter((wf) => wf.can_create));

onMounted(() => {
  if (!store.catalogue.loaded) void loadCatalogue();
});
</script>

<template>
  <div class="page">
    <header>
      <h1>Start something</h1>
      <p class="lede">
        Everything else in FoLT's chains is created from the document before it, already filled in —
        open that document and use its <strong>Create</strong> step.
      </p>
    </header>

    <div v-if="startable.length" class="grid">
      <RouterLink v-for="wf in startable" :key="wf.doctype" :to="`/new/${info(wf.doctype).slug}`" class="start card">
        <span class="icon"><Icon name="plus" :size="22" /></span>
        <span class="text">
          <span class="title">New {{ info(wf.doctype).noun.toLowerCase() }}</span>
          <span class="desc">{{ DESCRIPTIONS[wf.doctype] }}</span>
        </span>
        <Icon name="arrow" :size="18" class="go" />
      </RouterLink>
    </div>
    <EmptyState
      v-else-if="store.catalogue.loaded"
      icon="plus"
      title="Nothing to start from here"
      hint="Your roles do not raise an activity requisition or a waiver request. Documents reach you at the step you hold."
    />
  </div>
</template>

<style scoped>
.page {
  display: grid;
  gap: 1.5rem;
}
h1 {
  font-size: var(--text-3xl);
}
.lede {
  margin: 0.4rem 0 0;
  max-width: 44rem;
  color: var(--muted);
}
.grid {
  display: grid;
  gap: 1rem;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 24rem), 1fr));
}
.start {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1.2rem;
  color: inherit;
  text-decoration: none;
  transition: transform var(--dur-control) ease, border-color var(--dur-control) ease;
}
.start:hover {
  border-color: var(--brand);
  transform: translateY(-2px);
}
.icon {
  display: grid;
  place-items: center;
  flex: none;
  width: 44px;
  height: 44px;
  border-radius: var(--radius-md);
  background: var(--brand-tint-strong);
  color: var(--brand);
}
.text {
  display: grid;
  gap: 0.25rem;
  flex: 1;
}
.title {
  color: var(--heading);
  font-weight: var(--weight-semibold);
}
.desc {
  color: var(--muted);
  font-size: var(--text-sm);
}
.go {
  color: var(--faint);
}
</style>
