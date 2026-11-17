<script setup lang="ts">
// Who signed each step of this document, oldest first (approval_signatures). A return or rejection
// supersedes the pass before it: those steps stay listed but muted, because the document went round
// again and the next pass was signed afresh. Steps rebuilt from the timeline of a move made before
// signatures were recorded say so, and carry no signature.
import { when } from "../../lib/format";
import type { ApprovalStep } from "../../lib/types";

defineProps<{ steps: ApprovalStep[] }>();
</script>

<template>
  <ol v-if="steps.length" class="approvals">
    <li v-for="s in steps" :key="s.name" class="step" :class="{ superseded: !!s.superseded, down: !!s.turn_down }">
      <div class="who">
        <p class="action">
          {{ s.action }}
          <span v-if="s.superseded && !s.turn_down" class="tag">Superseded</span>
          <span v-if="s.backfilled" class="tag">From history</span>
        </p>
        <p class="name">{{ s.approver_name || s.approver }}<template v-if="s.designation || s.role"> · {{ s.designation || s.role }}</template></p>
        <p class="meta">{{ when(s.signed_on) }} · to {{ s.to_state }}</p>
        <blockquote v-if="s.reason" class="reason">{{ s.reason }}</blockquote>
      </div>
      <img v-if="s.signature" class="sig" :src="s.signature" :alt="`Signature of ${s.approver_name || s.approver}`" />
      <span v-else-if="!s.turn_down" class="nosig">Not signed</span>
    </li>
  </ol>
  <p v-else class="none">Nobody has signed this yet. Each approval adds the approver's name and signature here and on the print.</p>
</template>

<style scoped>
.approvals {
  display: grid;
  gap: 0.75rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.step {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.75rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--border);
}
.step:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}
.superseded {
  opacity: 0.55;
}
.who {
  min-width: 0;
}
.who p {
  margin: 0;
}
.action {
  color: var(--heading);
  font-weight: var(--weight-semibold);
}
.name,
.meta {
  color: var(--muted);
  font-size: var(--text-sm);
}
.tag {
  margin-left: 0.35rem;
  padding: 0 0.4rem;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-pill);
  color: var(--muted);
  font-size: var(--text-xs);
  font-weight: var(--weight-regular);
}
.reason {
  margin: 0.35rem 0 0;
  color: var(--muted);
  font-size: var(--text-sm);
  font-style: italic;
}
/* White in both themes: a drawn signature is dark ink on a transparent PNG. */
.sig {
  flex: 0 0 auto;
  max-width: 140px;
  max-height: 48px;
  padding: 2px 6px;
  background: #ffffff;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.nosig {
  flex: 0 0 auto;
  color: var(--faint);
  font-size: var(--text-xs);
}
.none {
  margin: 0;
  color: var(--muted);
  font-size: var(--text-sm);
}
</style>
