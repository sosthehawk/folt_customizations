<script setup lang="ts">
// Starting a chain: a new activity requisition or waiver request. The form is the server's
// (spa.new_form -> step_forms.CREATE_FORMS, with the defaults a new document gets), so the fields
// here are exactly the ones the Draft step edits later.
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import EmptyState from "../components/EmptyState.vue";
import FormAlert from "../components/FormAlert.vue";
import Icon from "../components/Icon.vue";
import SkeletonList from "../components/SkeletonList.vue";
import StepForm from "../components/forms/StepForm.vue";
import { errorText } from "../lib/api";
import { docPath, fromSlug, listPath } from "../lib/doctypes";
import { missing, useDraft } from "../lib/draft";
import { createDoc, newForm, type WriteResult } from "../lib/store";
import type { NewForm } from "../lib/types";

const props = defineProps<{ slug: string }>();
const router = useRouter();

const meta = computed(() => fromSlug(props.slug));
const spec = ref<NewForm | null>(null);
const loadError = ref<string | null>(null);
const draft = useDraft(() => spec.value);
const errors = ref<Record<string, string>>({});
const result = ref<(WriteResult & { name?: string }) | null>(null);
const busy = ref(false);

onMounted(async () => {
  if (!meta.value) return;
  try {
    spec.value = await newForm(meta.value.doctype);
  } catch (error) {
    loadError.value = errorText(error);
  }
});

function touched(fieldname: string) {
  if (errors.value[fieldname]) {
    const next = { ...errors.value };
    delete next[fieldname];
    errors.value = next;
  }
}

async function create() {
  if (!spec.value || !meta.value) return;
  // Send the defaults too: they are values the person saw and accepted, not edits they made.
  const values: Record<string, unknown> = {};
  for (const f of spec.value.fields) {
    if (f.fieldtype === "Attach") continue;
    const v = draft.value(f.fieldname);
    if (v !== null && v !== undefined && v !== "") values[f.fieldname] = v;
  }
  errors.value = missing(spec.value, draft.value);
  if (Object.keys(errors.value).length) return;
  busy.value = true;
  result.value = await createDoc(meta.value.doctype, values as never);
  busy.value = false;
  if (result.value.ok && result.value.name) router.replace(docPath(meta.value.doctype, result.value.name));
}

const attachments = computed(() => spec.value?.fields.filter((f) => f.fieldtype === "Attach") ?? []);
</script>

<template>
  <div class="page">
    <EmptyState v-if="!meta" icon="alert" title="Not something /folt raises" />
    <template v-else>
      <header>
        <RouterLink :to="listPath(meta.doctype)" class="back"><Icon name="back" :size="16" /> {{ meta.plural }}</RouterLink>
        <h1>New {{ meta.noun.toLowerCase() }}</h1>
      </header>
      <p v-if="loadError" class="card pad error" role="alert">{{ loadError }}</p>
      <SkeletonList v-else-if="!spec" :rows="3" height="4rem" />
      <form v-else class="card pad form" novalidate @submit.prevent="create">
        <StepForm :form="spec" :draft="draft" :doctype="meta.doctype" :errors="errors" :disabled="busy" @touched="touched" />
        <p v-if="attachments.length" class="later">
          {{ attachments.map((a) => a.label).join(", ") }} can be attached once it is saved.
        </p>
        <FormAlert v-if="result && !result.ok" :title="result.title" :html="result.error" />
        <div class="actions">
          <button type="submit" class="btn btn-primary big" :disabled="busy">{{ busy ? "Saving…" : `Save as draft` }}</button>
          <RouterLink :to="listPath(meta.doctype)" class="btn btn-quiet">Cancel</RouterLink>
        </div>
        <p class="after">It is saved as a draft for you to check, then send on from its own page.</p>
      </form>
    </template>
  </div>
</template>

<style scoped>
.page {
  display: grid;
  gap: 1.25rem;
  max-width: 56rem;
}
.back {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  margin-bottom: 0.3rem;
  color: var(--muted);
  font-size: var(--text-sm);
  text-decoration: none;
}
h1 {
  font-size: var(--text-3xl);
}
.pad {
  padding: 1.25rem;
}
.form {
  display: grid;
  gap: 1.25rem;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.big {
  min-height: 48px;
  padding: 0 1.5rem;
}
.later,
.after {
  margin: 0;
  color: var(--faint);
  font-size: var(--text-sm);
}
.error {
  color: var(--danger);
}
</style>
