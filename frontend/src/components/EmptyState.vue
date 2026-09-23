<script setup lang="ts">
// An empty queue is usually the healthy state of an approval chain, not a failure, so it gets a
// sentence saying what would put something here rather than a blank panel.
import Icon from "./Icon.vue";

defineProps<{ title: string; hint?: string; icon?: string }>();
</script>

<template>
  <div class="empty">
    <span v-if="icon" class="halo"><Icon :name="icon" :size="22" /></span>
    <p class="title">{{ title }}</p>
    <p v-if="hint" class="hint">{{ hint }}</p>
    <slot />
  </div>
</template>

<style scoped>
.empty {
  display: grid;
  justify-items: center;
  gap: 0.35rem;
  padding: 2.5rem 1.25rem;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-lg);
  background: var(--surface);
  text-align: center;
}
.halo {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--brand-tint-strong);
  color: var(--brand);
}
.title {
  margin: 0.4rem 0 0;
  color: var(--heading);
  font-weight: var(--weight-semibold);
}
.hint {
  margin: 0;
  max-width: 32rem;
  color: var(--muted);
  font-size: var(--text-sm);
}
</style>
