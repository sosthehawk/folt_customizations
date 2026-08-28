<script setup lang="ts">
// The Phase B spike. Deliberately not a product screen: it exists to prove the pipeline end to
// end -- the page renders, the bundle loads from a hashed path, the session is ours, a POST to a
// whitelisted method succeeds with the CSRF token, and a deep link reaches the same document.
// Everything product-shaped waits for Phase C.
import { onMounted, ref } from "vue";

import { boot } from "./lib/boot";
import { call, FrappeError } from "./lib/api";

const requisitions = ref<number | null>(null);
const error = ref<string | null>(null);

type Tasks = { counts: Record<string, number> };
const awaiting = ref<number | null>(null);

onMounted(async () => {
  try {
    // A read, through the same POST path everything else uses.
    requisitions.value = await call<number>("frappe.client.get_count", {
      doctype: "Activity Requisition",
    });

    // A FoLT endpoint, which is the one that actually proves the CSRF token is accepted:
    // frappe.client.* is whitelisted with allow_guest=False but my_tasks is ours, and it is the
    // inbox Phase C is built on.
    const tasks = await call<Tasks>(
      "folt_customizations.folt_customizations.page.folt_tasks.folt_tasks.my_tasks",
      { bucket: "awaiting" },
    );
    awaiting.value = tasks?.counts?.awaiting ?? 0;
  } catch (e) {
    error.value = e instanceof FrappeError ? e.message : String(e);
  }
});
</script>

<template>
  <main class="spike">
    <h1>FoLT</h1>
    <p class="who">{{ boot.full_name }} &middot; {{ boot.user }}</p>

    <dl>
      <dt>Activity Requisitions</dt>
      <dd>{{ requisitions ?? "…" }}</dd>

      <dt>Awaiting me</dt>
      <dd>{{ awaiting ?? "…" }}</dd>

      <dt>Roles</dt>
      <dd>{{ boot.roles.filter((r) => r !== "All" && r !== "Guest").join(", ") }}</dd>

      <dt>Deep-link path</dt>
      <dd><code>{{ boot.app_path || "(none)" }}</code></dd>
    </dl>

    <p v-if="error" class="error">{{ error }}</p>

    <p class="desk"><a href="/desk/folt-tasks">Open My Tasks in the Desk</a></p>
  </main>
</template>

<style>
/* Document-level, deliberately NOT scoped: a scoped block cannot reach <body>, and a page that
   declares `color-scheme: light dark` (see www/folt.html) gets the browser's dark ground while
   painting its own text -- which is how the first render of this spike came out near-black on
   near-black. Declare both grounds explicitly or declare neither.

   Tokens rather than literals, and the same names folt_desk.css uses, so Phase C can lift the
   palette across without renaming anything. */
:root {
  --folt-bg: #ffffff;
  --folt-fg: #171717;
  --folt-muted: #7c7c7c;
  --folt-danger-bg: #fff4f4;
  --folt-danger-fg: #b00020;
  --folt-link: #3c6a91; /* branding.EMAIL_ACCENT -- the wordmark blue */
}

@media (prefers-color-scheme: dark) {
  :root {
    --folt-bg: #171717;
    --folt-fg: #f4f4f4;
    --folt-muted: #a0a0a0;
    --folt-danger-bg: #2b1416;
    --folt-danger-fg: #ff9c9c;
    --folt-link: #8ab4d8;
  }
}

body {
  margin: 0;
  background: var(--folt-bg);
  color: var(--folt-fg);
}
</style>

<style scoped>
.spike {
  font-family:
    -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  max-width: 34rem;
  margin: 0 auto;
  padding: 2rem 1.25rem;
  color: var(--folt-fg);
}
h1 {
  margin: 0;
  font-size: 1.5rem;
}
.who {
  margin: 0.25rem 0 1.5rem;
  color: var(--folt-muted);
  font-size: 0.875rem;
}
dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.5rem 1rem;
  margin: 0;
  font-size: 0.9375rem;
}
dt {
  color: var(--folt-muted);
}
dd {
  margin: 0;
  font-weight: 500;
}
.error {
  margin-top: 1.5rem;
  padding: 0.75rem 1rem;
  border-radius: 0.5rem;
  background: var(--folt-danger-bg);
  color: var(--folt-danger-fg);
  font-size: 0.875rem;
}
.desk {
  margin-top: 2rem;
  font-size: 0.875rem;
}
.desk a {
  color: var(--folt-link);
}
</style>
