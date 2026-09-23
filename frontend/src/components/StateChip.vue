<script setup lang="ts">
// A workflow state, coloured by what it means. The tone is the server's (spa.state_tones, derived
// from the workflow's shape), so a chip here and the same state anywhere else in /folt agree, and
// Draft does not come out red just because workflow_state.json styles it "Danger".
import type { Tone } from "../lib/types";

withDefaults(defineProps<{ state: string | null | undefined; tone?: Tone; size?: "sm" | "md" }>(), {
  tone: "plain",
  size: "md",
});
</script>

<template>
  <span v-if="state" class="chip" :class="[`tone-${tone}`, `size-${size}`]">
    <span class="dot" aria-hidden="true" />{{ state }}
  </span>
</template>

<style scoped>
.chip {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  border: 1px solid color-mix(in srgb, currentColor 28%, transparent);
  border-radius: var(--radius-pill);
  font-weight: var(--weight-medium);
  white-space: nowrap;
  line-height: 1.3;
}
.size-md {
  padding: 0.2rem 0.65rem;
  font-size: var(--text-xs);
}
.size-sm {
  padding: 0.05rem 0.5rem;
  font-size: 12px;
}
/* The dot is a second, non-colour cue for the shape (WCAG 1.4.1) -- the words are the first. */
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.tone-ok { background: var(--ok-bg); color: var(--ok); }
.tone-warn { background: var(--warn-bg); color: var(--warn); }
.tone-danger { background: var(--danger-bg); color: var(--danger); }
.tone-info { background: var(--info-bg); color: var(--info); }
.tone-plain { background: var(--plain-bg); color: var(--plain); }
</style>
