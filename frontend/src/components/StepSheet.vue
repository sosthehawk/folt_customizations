<script setup lang="ts">
// The one dialog shell: a bottom sheet on a phone, a centred dialog on a wide screen. Everything a
// dialog must get right and usually does not lives here rather than in each form -- the backdrop
// swallows the click, Escape closes, focus moves in on open and back on close, Tab cannot leave,
// and the page behind does not scroll. A sheet rather than a route for a short step: the tracker
// stays visible behind it and the step is over in one gesture.
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";

import Icon from "./Icon.vue";

const props = defineProps<{ title: string; subtitle?: string; busy?: boolean }>();
const emit = defineEmits<{ close: [] }>();

const panel = ref<HTMLElement | null>(null);
let returnTo: HTMLElement | null = null;

function close() {
  if (props.busy) return; // never pull a sheet out from under a save in flight
  emit("close");
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.stopPropagation();
    close();
    return;
  }
  if (event.key !== "Tab" || !panel.value) return;
  const focusable = panel.value.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  );
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

onMounted(async () => {
  returnTo = document.activeElement as HTMLElement | null;
  document.addEventListener("keydown", onKeydown, true);
  document.body.style.overflow = "hidden";
  await nextTick();
  // The first control, not the close button: the point of opening this is to type in it.
  panel.value?.querySelector<HTMLElement>(".body input, .body textarea, .body select, footer button")?.focus();
});

onBeforeUnmount(() => {
  document.removeEventListener("keydown", onKeydown, true);
  document.body.style.overflow = "";
  returnTo?.focus?.();
});
</script>

<template>
  <div class="scrim" @click.self="close">
    <section ref="panel" class="panel" role="dialog" aria-modal="true" :aria-label="title" :aria-busy="busy ? 'true' : 'false'">
      <header>
        <div class="titles">
          <h2>{{ title }}</h2>
          <p v-if="subtitle" class="sub">{{ subtitle }}</p>
        </div>
        <button type="button" class="x" aria-label="Close" :disabled="busy" @click="close"><Icon name="x" :size="18" /></button>
      </header>
      <div class="body"><slot /></div>
      <footer><slot name="actions" /></footer>
    </section>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  background: rgb(0 26 51 / 0.45);
  animation: fade 0.2s ease;
}
.panel {
  display: flex;
  flex-direction: column;
  width: 100%;
  max-height: 92vh;
  border-radius: var(--radius-xl) var(--radius-xl) 0 0;
  background: var(--surface);
  box-shadow: var(--shadow-lift);
  padding-bottom: env(safe-area-inset-bottom, 0);
  animation: rise var(--dur-rise) var(--ease-out);
}
@media (min-width: 40rem) {
  .scrim {
    align-items: center;
  }
  .panel {
    width: min(34rem, calc(100vw - 3rem));
    border-radius: var(--radius-xl);
  }
}
header {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 1.1rem 1.1rem 0.8rem;
  border-bottom: 1px solid var(--border);
}
.titles {
  min-width: 0;
}
h2 {
  font-size: var(--text-lg);
}
.sub {
  margin: 0.15rem 0 0;
  color: var(--muted);
  font-size: var(--text-sm);
  overflow-wrap: anywhere;
}
.x {
  display: grid;
  place-items: center;
  margin-left: auto;
  flex: none;
  width: 36px;
  height: 36px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--muted);
  cursor: pointer;
}
.x:hover:not(:disabled) {
  background: var(--surface-sunk);
  color: var(--fg);
}
.body {
  display: grid;
  gap: 1rem;
  padding: 1.1rem;
  overflow-y: auto;
}
footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.5rem;
  padding: 0.85rem 1.1rem 1.1rem;
  border-top: 1px solid var(--border);
}
@keyframes rise {
  from { transform: translateY(24px); opacity: 0; }
}
@keyframes fade {
  from { opacity: 0; }
}
</style>
