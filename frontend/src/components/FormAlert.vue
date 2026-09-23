<script setup lang="ts">
// A refusal from the server, INSIDE the form or sheet that caused it. FoLT's rules are stated in
// these messages and they are frequently the only statement of the rule, so they render as the
// server wrote them (sanitised), not as a toast that vanishes or a banner behind a scrim.
import Icon from "./Icon.vue";
import SafeHtml from "./SafeHtml.vue";

defineProps<{ title?: string | null; html: string | null; stale?: boolean }>();
const emit = defineEmits<{ reload: [] }>();
</script>

<template>
  <div v-if="html" class="alert" role="alert">
    <Icon name="alert" :size="18" class="icon" />
    <div class="text">
      <strong v-if="title">{{ title }}</strong>
      <SafeHtml :html="html" tag="div" />
      <button v-if="stale" type="button" class="btn btn-secondary reload" @click="emit('reload')">Reload the document</button>
    </div>
  </div>
</template>

<style scoped>
.alert {
  display: flex;
  gap: 0.6rem;
  padding: 0.8rem 0.9rem;
  border: 1px solid color-mix(in srgb, var(--danger) 35%, transparent);
  border-radius: var(--radius-md);
  background: var(--danger-bg);
  color: var(--fg);
  font-size: var(--text-sm);
}
.icon {
  color: var(--danger);
  margin-top: 1px;
}
.text {
  display: grid;
  gap: 0.3rem;
  min-width: 0;
  overflow-wrap: anywhere;
}
.reload {
  justify-self: start;
  margin-top: 0.3rem;
}
</style>
