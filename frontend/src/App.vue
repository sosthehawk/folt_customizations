<script setup lang="ts">
// The shell: navigation, whatever the route shows, and the server notices.
//
// The realtime connection lives here rather than in a view because it has to outlive every
// navigation -- a socket torn down and rebuilt on each route change would miss exactly the
// events that arrive while somebody is reading. It does not know what is on screen: it bumps
// store.pulse and each view reloads its own data.

import { onBeforeUnmount, onMounted } from "vue";

import AppNav from "./components/AppNav.vue";
import ToastStack from "./components/ToastStack.vue";
import { isLive, on as onRealtime, start, stop } from "./lib/realtime";
import { loadBell, loadTasks, pulse } from "./lib/store";
import { watchSystem } from "./lib/theme";

function refresh() {
  void loadBell();
  void loadTasks("awaiting");
  pulse();
}

onMounted(async () => {
  // The inline script in www/folt.html painted the right theme already; this takes it over.
  watchSystem();
  // The nav shows the awaiting count and the bell on every screen, so both load once here.
  await Promise.all([loadTasks("awaiting"), loadBell()]);
  onRealtime("notification", refresh);
  onRealtime("doc_update", () => pulse());
  onRealtime("folt_state_derived", () => pulse());
  void start(refresh);
});

onBeforeUnmount(stop);
</script>

<template>
  <a class="skip" href="#main">Skip to content</a>
  <AppNav />
  <main id="main" class="page">
    <RouterView v-slot="{ Component }">
      <component :is="Component" />
    </RouterView>
  </main>
  <ToastStack />
  <p class="sr-only" aria-live="polite">{{ isLive() ? "Live updates on" : "Updating periodically" }}</p>
</template>

<style scoped>
.page {
  max-width: 76rem;
  margin: 0 auto;
  /* 16px side gutter at phone width, never a horizontal scrollbar; the bottom padding clears the
     fixed tab bar plus the home indicator, or the last card sits under the nav. */
  padding: 1.5rem 1rem calc(5.5rem + env(safe-area-inset-bottom, 0));
}
@media (min-width: 48rem) {
  .page {
    padding: 2rem 1.5rem 4rem;
  }
}
.skip {
  position: absolute;
  left: -999px;
  top: 0.5rem;
  z-index: 100;
  padding: 0.5rem 1rem;
  border-radius: var(--radius-md);
  background: var(--action);
  color: var(--action-fg);
}
.skip:focus {
  left: 0.5rem;
}
</style>
