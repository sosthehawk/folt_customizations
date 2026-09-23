<script setup lang="ts">
// The scalar fields of one step, laid out in a responsive grid, bound to the draft.
//
// Generic on purpose, and driven entirely by the server's form spec (step_forms.STEP_FORMS plus
// the live meta): nine workflows' worth of steps with one component, so a step that gains a field
// in the registry gains it here with no client change. Attach fields are not here -- they upload
// on their own through the evidence list, because a file is not a value to hold in a draft.
import { computed, reactive } from "vue";

import { shown, type Draft } from "../../lib/draft";
import type { Form, PickerOption } from "../../lib/types";
import FieldRow from "../FieldRow.vue";
import FieldControl from "./FieldControl.vue";

const props = defineProps<{
  form: Form;
  draft: Draft;
  doctype: string;
  docName?: string | null;
  errors?: Record<string, string>;
  disabled?: boolean;
}>();
const emit = defineEmits<{ touched: [string] }>();

const WIDE = new Set(["Small Text", "Text", "Long Text", "Text Editor"]);

const fields = computed(() =>
  [...props.form.fields, ...props.form.virtual].filter(
    (f) => f.fieldtype !== "Attach" && f.fieldtype !== "Attach Image" && shown(props.form.show_if[f.fieldname], props.draft.value),
  ),
);

// Every current value goes to the pickers, so a link_filters rule like `eval:doc.activity` and the
// four FoLT custom queries (payable floats, verified registers...) can narrow what they offer.
const context = computed(() => {
  const out: Record<string, unknown> = { name: props.docName ?? null };
  for (const f of [...props.form.fields, ...props.form.virtual]) out[f.fieldname] = props.draft.value(f.fieldname);
  return out;
});

function update(fieldname: string, value: unknown) {
  props.draft.set(fieldname, value as never);
  emit("touched", fieldname);
}

// The label a picker last chose, so a re-render shows "Nairobi Office", not "LOC-0007".
const labels = reactive<Record<string, string | null>>({});
function chosen(fieldname: string, option: PickerOption | null) {
  labels[fieldname] = option?.label ?? null;
}
</script>

<template>
  <div v-if="fields.length" class="grid">
    <FieldRow
      v-for="field in fields"
      :id="`f-${field.fieldname}`"
      :key="field.fieldname"
      :label="field.label"
      :hint="field.description"
      :error="errors?.[field.fieldname]"
      :required="field.reqd"
      :class="{ wide: WIDE.has(field.fieldtype) }"
    >
      <FieldControl
        :id="`f-${field.fieldname}`"
        :spec="field"
        :value="draft.value(field.fieldname)"
        :display="labels[field.fieldname] ?? field.display"
        :doctype="doctype"
        :picker-field="field.fieldname"
        :context="context"
        :invalid="!!errors?.[field.fieldname]"
        :disabled="disabled"
        @update:value="(v) => update(field.fieldname, v)"
        @chosen="(o) => chosen(field.fieldname, o)"
      />
    </FieldRow>
  </div>
</template>

<style scoped>
.grid {
  display: grid;
  gap: 1rem 1.25rem;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 16rem), 1fr));
}
.wide {
  grid-column: 1 / -1;
}
</style>
