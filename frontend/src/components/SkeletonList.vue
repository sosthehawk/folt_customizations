<script setup lang="ts">
// Placeholder rows, not the word "Loading": a list that resolves into the shape it was holding
// does not make the page jump, which on a phone is the difference between reading and re-finding.
withDefaults(defineProps<{ rows?: number; height?: string }>(), { rows: 3, height: "4.5rem" });
</script>

<template>
  <div class="skeleton" aria-busy="true" aria-live="polite">
    <span class="sr-only">Loading…</span>
    <div v-for="row in rows" :key="row" class="bar" :style="{ height }" />
  </div>
</template>

<style scoped>
.skeleton {
  display: grid;
  gap: 0.75rem;
}
.bar {
  border-radius: var(--radius-lg);
  background: linear-gradient(90deg, var(--surface-sunk) 25%, var(--surface) 50%, var(--surface-sunk) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.4s infinite;
}
@keyframes shimmer {
  to { background-position: -200% 0; }
}
</style>
