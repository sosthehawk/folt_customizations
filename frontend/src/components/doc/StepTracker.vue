<script setup lang="ts">
// Where the document is in its chain: every step, which are done, which one it is on.
//
// Status travels three ways, never by colour alone: the mark (a tick, a ring, an empty circle),
// the words under it, and an aria-current on the step it is at. Steps come from workflow_shape's
// derived plan, so an optional state (Overdue, Partly Paid) is a note on a step, not a step --
// drawing Overdue as step 5 would say every float goes overdue.
import { computed } from "vue";

import { roles } from "../../lib/format";
import type { Guide } from "../../lib/types";
import Icon from "../Icon.vue";
import SafeHtml from "../SafeHtml.vue";

const props = defineProps<{ guide: Guide }>();

const steps = computed(() => props.guide.steps ?? []);
</script>

<template>
  <div class="tracker">
    <ol class="steps" :aria-label="`Step ${(guide.lane ?? 0) + 1} of ${guide.of}`">
      <li
        v-for="step in steps"
        :key="step.rank"
        class="step"
        :class="`is-${step.status}`"
        :aria-current="step.status === 'current' ? 'step' : undefined"
      >
        <span class="mark">
          <Icon v-if="step.status === 'done'" name="check" :size="14" />
          <span v-else class="num">{{ step.rank + 1 }}</span>
        </span>
        <span class="label">{{ step.label }}</span>
        <span v-if="step.status === 'current' && guide.at_optional" class="sub">at {{ guide.at_optional }}</span>
        <span v-else-if="step.status !== 'done' && step.roles.length && !step.terminal" class="sub">{{ roles(step.roles) }}</span>
        <span v-if="step.optional.length && step.status !== 'current'" class="sub opt">or via {{ step.optional.join(", ") }}</span>
      </li>
    </ol>

    <div v-if="guide.off_path?.kind === 'turned_down'" class="banner danger">
      <Icon name="alert" :size="18" />
      <div>
        <strong>Turned down</strong>
        <SafeHtml v-if="guide.rejection_reason" :html="guide.rejection_reason" tag="p" class="reason" />
      </div>
    </div>
    <div v-else-if="guide.off_path?.kind === 'detour'" class="banner warn">
      <Icon name="alert" :size="18" />
      <div><strong>{{ guide.off_path.state }}</strong> — off the main path, with {{ roles(guide.off_path.roles) }}.</div>
    </div>
    <div v-else-if="guide.rejection_reason" class="banner warn">
      <Icon name="undo" :size="18" />
      <div>
        <strong>Sent back for correction</strong>
        <SafeHtml :html="guide.rejection_reason" tag="p" class="reason" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.tracker {
  display: grid;
  gap: 0.9rem;
}
.steps {
  --mark: 28px;
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(7.5rem, 1fr);
  margin: 0;
  padding: 0 0 4px;
  list-style: none;
  overflow-x: auto;
  scroll-snap-type: x proximity;
  scrollbar-width: thin;
}
.step {
  position: relative;
  display: grid;
  justify-items: center;
  align-content: start;
  gap: 0.3rem;
  padding: 0 0.35rem;
  text-align: center;
  scroll-snap-align: start;
}
/* The connector runs from this mark to the next one. */
.step:not(:last-child)::after {
  content: "";
  position: absolute;
  top: calc(var(--mark) / 2 - 1px);
  left: calc(50% + var(--mark) / 2 + 4px);
  right: calc(-50% + var(--mark) / 2 + 4px);
  height: 2px;
  border-radius: 2px;
  background: var(--border-strong);
}
.step.is-done:not(:last-child)::after {
  background: var(--brand);
}
.mark {
  display: grid;
  place-items: center;
  width: var(--mark);
  height: var(--mark);
  border: 2px solid var(--border-strong);
  border-radius: 50%;
  background: var(--surface);
  color: var(--faint);
  font-size: 12px;
  font-weight: 700;
}
.is-done .mark {
  border-color: var(--brand);
  background: var(--brand);
  color: var(--action-fg);
}
.is-current .mark {
  border-color: var(--brand);
  color: var(--brand);
  box-shadow: 0 0 0 4px var(--brand-tint-strong);
}
.label {
  color: var(--muted);
  font-size: var(--text-sm);
  line-height: 1.3;
}
.is-current .label {
  color: var(--heading);
  font-weight: var(--weight-semibold);
}
.sub {
  color: var(--faint);
  font-size: 12px;
  line-height: 1.3;
}
.opt {
  font-style: italic;
}
@media (max-width: 40rem) {
  .steps {
    grid-auto-flow: row;
    grid-auto-columns: auto;
    gap: 0.9rem;
    overflow: visible;
  }
  .step {
    grid-template-columns: var(--mark) minmax(0, 1fr);
    justify-items: start;
    column-gap: 0.75rem;
    row-gap: 0.1rem;
    padding: 0;
    text-align: left;
  }
  .step .mark {
    grid-row: span 3;
  }
  .step:not(:last-child)::after {
    top: calc(var(--mark) + 4px);
    bottom: -0.9rem;
    left: calc(var(--mark) / 2 - 1px);
    right: auto;
    width: 2px;
    height: auto;
  }
}
.banner {
  display: flex;
  gap: 0.6rem;
  padding: 0.75rem 0.9rem;
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
}
.banner.danger {
  background: var(--danger-bg);
  color: var(--danger);
  border: 1px solid color-mix(in srgb, var(--danger) 30%, transparent);
}
.banner.warn {
  background: var(--warn-bg);
  color: var(--warn);
  border: 1px solid color-mix(in srgb, var(--warn) 30%, transparent);
}
.banner strong {
  color: inherit;
}
.reason {
  margin: 0.25rem 0 0;
  color: var(--fg);
}
</style>
