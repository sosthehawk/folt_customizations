<script setup lang="ts">
// Server notices that arrive with a SUCCESSFUL write -- "Withdrawn from 2 committee evaluations",
// "Another draft on this register". A failed write shows its message inside the form instead.
import { dismiss, store } from "../lib/store";
import Icon from "./Icon.vue";
import SafeHtml from "./SafeHtml.vue";
</script>

<template>
  <div class="stack" aria-live="polite" role="status">
    <TransitionGroup name="toast">
      <div v-for="t in store.toasts" :key="t.id" class="toast" :class="`tone-${t.tone}`">
        <SafeHtml :html="t.html" tag="div" class="body" />
        <button type="button" class="close" aria-label="Dismiss" @click="dismiss(t.id)">
          <Icon name="x" :size="16" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.stack {
  position: fixed;
  right: 1rem;
  bottom: 1rem;
  z-index: 70;
  display: grid;
  gap: 0.5rem;
  width: min(26rem, calc(100vw - 2rem));
}
@media (max-width: 48rem) {
  .stack {
    bottom: calc(64px + env(safe-area-inset-bottom, 0));
  }
}
.toast {
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
  padding: 0.8rem 0.9rem;
  border: 1px solid var(--border);
  border-left: 4px solid var(--brand);
  border-radius: var(--radius-md);
  background: var(--surface);
  box-shadow: var(--shadow-lift);
  font-size: var(--text-sm);
}
.tone-ok { border-left-color: var(--ok); }
.tone-danger { border-left-color: var(--danger); }
.body {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}
.close {
  border: 0;
  background: none;
  color: var(--faint);
  cursor: pointer;
  padding: 2px;
}
.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.2s ease, transform 0.2s var(--ease-out);
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
