<script setup lang="ts">
// The evidence this document needs, whether it is here, and what it gates -- document_guide's
// DOCUMENTS registry, which is what turns "Attach the signed list before marking it paid" from a
// throw at the button into a chip visible the moment the document is opened.
import type { Evidence } from "../../lib/types";
import AttachField from "../AttachField.vue";

defineProps<{ items: Evidence[]; editable: Set<string>; busy: string | null }>();
const emit = defineEmits<{ upload: [string, File] }>();
</script>

<template>
  <ul class="evidence">
    <li v-for="item in items" :key="item.fieldname" class="item" :class="{ blocking: item.blocks_next }">
      <div class="what">
        <span class="label">{{ item.label }}</span>
        <span v-if="item.attached" class="pill ok">Attached</span>
        <span v-else-if="item.blocks_next" class="pill danger">Needed before {{ item.blocks.join(", ") }}</span>
        <span v-else-if="item.advisory" class="pill plain">Expected</span>
        <span v-else class="pill warn">Needed at {{ item.required_at.join(", ") }}</span>
      </div>
      <p v-if="item.description" class="why">{{ item.description }}</p>
      <AttachField
        :id="`evidence-${item.fieldname}`"
        :url="item.url"
        :editable="editable.has(item.fieldname)"
        :busy="busy === item.fieldname"
        @upload="(file) => emit('upload', item.fieldname, file)"
      />
    </li>
  </ul>
</template>

<style scoped>
.evidence {
  display: grid;
  gap: 0.6rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.item {
  display: grid;
  gap: 0.45rem;
  padding: 0.75rem 0.9rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.item.blocking {
  border-color: color-mix(in srgb, var(--danger) 45%, var(--border));
}
.what {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.4rem;
}
.label {
  color: var(--heading);
  font-weight: var(--weight-medium);
}
.why {
  margin: 0;
  color: var(--faint);
  font-size: var(--text-xs);
}
.pill {
  padding: 0.1rem 0.55rem;
  border: 1px solid color-mix(in srgb, currentColor 30%, transparent);
  border-radius: var(--radius-pill);
  font-size: 12px;
  font-weight: var(--weight-medium);
}
.pill.ok { background: var(--ok-bg); color: var(--ok); }
.pill.danger { background: var(--danger-bg); color: var(--danger); }
.pill.warn { background: var(--warn-bg); color: var(--warn); }
.pill.plain { background: var(--plain-bg); color: var(--plain); }
</style>
