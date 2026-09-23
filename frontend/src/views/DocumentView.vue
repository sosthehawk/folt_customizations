<script setup lang="ts">
// One document: a tracker with an action bar, not a form with fields missing.
//
// Everything on it comes from one call (spa.document): the Desk's own guide (tracker, timeline,
// evidence, hand-offs), the actions THIS user can take, the step's editable fields, and the
// read-only details. Edits are a draft diff; saving or acting sends only that diff, and the
// response replaces the whole document -- nothing here guesses what the server did.
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import EmptyState from "../components/EmptyState.vue";
import FormAlert from "../components/FormAlert.vue";
import Icon from "../components/Icon.vue";
import SkeletonList from "../components/SkeletonList.vue";
import StateChip from "../components/StateChip.vue";
import ActSheet from "../components/doc/ActSheet.vue";
import ActionBar from "../components/doc/ActionBar.vue";
import DetailsList from "../components/doc/DetailsList.vue";
import EvidenceList from "../components/doc/EvidenceList.vue";
import HandoffList from "../components/doc/HandoffList.vue";
import StepTools from "../components/doc/StepTools.vue";
import StepTracker from "../components/doc/StepTracker.vue";
import Timeline from "../components/doc/Timeline.vue";
import RowsEditor from "../components/forms/RowsEditor.vue";
import StepForm from "../components/forms/StepForm.vue";
import { fromSlug, info, listPath } from "../lib/doctypes";
import { missing, useDraft } from "../lib/draft";
import { money, roles } from "../lib/format";
import { watchDoc } from "../lib/realtime";
import { actOn, attachTo, docFor, loadDoc, saveDoc, store, toast, type WriteResult } from "../lib/store";
import type { Action } from "../lib/types";

const props = defineProps<{ slug: string; name: string }>();

const meta = computed(() => fromSlug(props.slug));
const doctype = computed(() => meta.value?.doctype ?? "");
const target = computed(() => docFor(doctype.value, props.name));
const doc = computed(() => target.value.data);
const guide = computed(() => doc.value?.guide);
const form = computed(() => doc.value?.form ?? null);

const draft = useDraft(() => form.value);
const dirty = computed(() => draft.dirty.value);
const errors = ref<Record<string, string>>({});
const saveResult = ref<WriteResult | null>(null);
const busy = ref(false);
const pending = ref<Action | null>(null);
const actResult = ref<WriteResult | null>(null);
const uploading = ref<string | null>(null);

const tone = computed(() => (doc.value?.state ? doc.value.tones[doc.value.state] : "plain"));
const currentStep = computed(() => guide.value?.steps.find((s) => s.status === "current"));
const hasStep = computed(() => !!doc.value && (doc.value.actions.length > 0 || form.value?.editable));
const attachable = computed(
  () => new Set(form.value?.editable ? form.value.fields.filter((f) => f.fieldtype === "Attach").map((f) => f.fieldname) : []),
);

function reload() {
  if (doctype.value) void loadDoc(doctype.value, props.name);
}

onMounted(() => {
  reload();
  watchDoc(doctype.value, props.name);
});
onBeforeUnmount(() => watchDoc(null, null));
watch(
  () => [doctype.value, props.name],
  () => {
    draft.reset();
    errors.value = {};
    saveResult.value = null;
    reload();
    watchDoc(doctype.value, props.name);
  },
);
// A realtime nudge or poll refreshes the document -- but never under somebody's unsaved edits,
// which the refresh would not lose (they are a diff) but would silently re-base.
watch(() => store.pulse, () => {
  if (!dirty.value && !busy.value) reload();
});

function validate(): boolean {
  if (!form.value) return true;
  errors.value = missing(form.value, draft.value);
  return !Object.keys(errors.value).length;
}

function touched(fieldname: string) {
  if (errors.value[fieldname]) {
    const next = { ...errors.value };
    delete next[fieldname];
    errors.value = next;
  }
}

async function save() {
  if (!doc.value || !validate()) return;
  busy.value = true;
  saveResult.value = await saveDoc(doc.value, draft.patch());
  busy.value = false;
  if (saveResult.value.ok) {
    draft.reset();
    saveResult.value = null;
    toast(`Saved ${doc.value?.name}.`, "ok");
  }
}

function onAct(action: Action) {
  if (dirty.value && !validate()) return;
  actResult.value = null;
  // Confirm what cannot be undone and ask for what a turn-down must carry. A plain forward move
  // with nothing unsaved is one tap.
  if (action.needs_reason || action.submits || dirty.value) pending.value = action;
  else void act(action);
}

async function act(action: Action, reason?: string) {
  if (!doc.value) return;
  busy.value = true;
  const before = doc.value.state;
  actResult.value = await actOn(doc.value, action.action, reason, dirty.value ? draft.patch() : undefined);
  busy.value = false;
  if (actResult.value.ok) {
    draft.reset();
    pending.value = null;
    toast(`${action.label}: ${doc.value?.name} moved from ${before} to ${doc.value?.state}.`, "ok");
  }
}

async function upload(fieldname: string, file: File) {
  if (!doc.value) return;
  if (dirty.value) {
    saveResult.value = { ok: false, title: "Save first", error: "Save your changes before attaching a file — the upload saves the document.", stale: false };
    return;
  }
  uploading.value = fieldname;
  saveResult.value = await attachTo(doc.value, fieldname, file);
  uploading.value = null;
  if (saveResult.value.ok) {
    saveResult.value = null;
    toast(`Attached ${file.name}.`, "ok");
  }
}

function discard() {
  draft.reset();
  errors.value = {};
  saveResult.value = null;
}
</script>

<template>
  <div class="doc">
    <EmptyState v-if="!meta" icon="alert" title="Not a FoLT document" hint="This link does not point at a document /folt serves." />
    <SkeletonList v-else-if="!target.loaded && target.loading" :rows="4" height="7rem" />
    <EmptyState v-else-if="target.error && !doc" icon="alert" title="This document could not be opened" :hint="target.error">
      <RouterLink :to="listPath(doctype)" class="btn btn-secondary">Back to {{ meta.plural.toLowerCase() }}</RouterLink>
    </EmptyState>

    <template v-else-if="doc && guide">
      <header class="head">
        <RouterLink :to="listPath(doc.doctype)" class="back"><Icon name="back" :size="16" /> {{ info(doc.doctype).plural }}</RouterLink>
        <div class="titles">
          <p class="kicker">{{ info(doc.doctype).noun }} · {{ doc.name }}</p>
          <h1>{{ doc.title }}</h1>
          <div class="chips">
            <StateChip :state="doc.state" :tone="tone" />
            <span v-if="guide.lane !== null && guide.of" class="step-of">Step {{ guide.lane + 1 }} of {{ guide.of }}</span>
            <span v-if="guide.chain" class="chain">Activity chain · {{ guide.chain.step }} of {{ guide.chain.of }}: {{ guide.chain.step_title }}</span>
          </div>
        </div>
        <a :href="doc.desk_url" class="btn btn-quiet desk" target="_blank" rel="noopener">Open in Desk <Icon name="external" :size="14" /></a>
      </header>

      <div class="layout">
        <div class="main">
          <section v-if="guide.steps.length" class="card pad">
            <StepTracker :guide="guide" />
          </section>
          <section v-else-if="guide.note" class="card pad note">{{ guide.note }}</section>

          <section v-if="hasStep && form" class="card pad step" aria-labelledby="your-step">
            <div class="step-head">
              <h2 id="your-step">{{ doc.actions.length ? "It's with you" : "Your step" }}</h2>
              <span v-if="currentStep" class="step-name">{{ currentStep.label }}</span>
            </div>
            <p v-if="!form.editable && form.why_not && !form.act_only" class="why">{{ form.why_not }}</p>

            <StepTools v-if="form.editable && form.tools.length" :doc="doc" :draft="draft" :tools="form.tools" />
            <StepForm
              v-if="form.editable"
              :form="form"
              :draft="draft"
              :doctype="doc.doctype"
              :doc-name="doc.name"
              :errors="errors"
              :disabled="busy"
              @touched="touched"
            />
            <RowsEditor
              v-for="t in form.tables"
              :key="t.fieldname"
              :table="t"
              :draft="draft"
              :doctype="doc.doctype"
              :doc-name="doc.name"
              :currency="doc.summary[0]?.currency"
              :disabled="busy || !form.editable"
            />
            <FormAlert v-if="saveResult && !saveResult.ok" :title="saveResult.title" :html="saveResult.error" :stale="saveResult.stale" @reload="reload" />

            <div v-if="dirty && !doc.actions.length" class="save-row">
              <button type="button" class="btn btn-primary" :disabled="busy" @click="save">{{ busy ? "Saving…" : "Save" }}</button>
              <button type="button" class="btn btn-quiet" :disabled="busy" @click="discard">Discard changes</button>
            </div>
            <ActionBar v-if="doc.actions.length" :actions="doc.actions" :dirty="dirty" :can-save="form.editable" :busy="busy" @act="onAct" @save="save" />
            <button v-if="dirty && doc.actions.length" type="button" class="btn btn-quiet discard" :disabled="busy" @click="discard">Discard changes</button>
            <FormAlert v-if="!pending && actResult && !actResult.ok" :title="actResult.title" :html="actResult.error" :stale="actResult.stale" @reload="reload" />
          </section>

          <section v-if="guide.documents.length" class="card pad">
            <h2 class="section-title">Evidence</h2>
            <EvidenceList :items="guide.documents" :editable="attachable" :busy="uploading" @upload="upload" />
          </section>

          <section v-if="guide.handoffs.length" class="card pad">
            <h2 class="section-title">Next in the chain</h2>
            <HandoffList :doc="doc" :handoffs="guide.handoffs" />
          </section>

          <section v-if="doc.summary.length" class="card pad">
            <h2 class="section-title">Details</h2>
            <DetailsList :sections="doc.summary" />
          </section>
        </div>

        <aside class="aside">
          <section v-if="guide.waiting_for.roles.length && !doc.actions.length" class="card pad">
            <h2 class="section-title">Waiting on</h2>
            <p class="who">{{ roles(guide.waiting_for.roles) }}</p>
            <ul v-if="guide.waiting_for.approvers.length" class="approvers">
              <li v-for="a in guide.waiting_for.approvers.slice(0, 6)" :key="a.user">{{ a.full_name }} <span class="faint">· {{ a.role }}</span></li>
            </ul>
            <p v-if="guide.waiting_for.unassigned" class="warn">Nobody holds this role, so this document is stuck. Ask an administrator to assign it.</p>
          </section>

          <section v-if="doc.context.authority?.length" class="card pad">
            <h2 class="section-title">Authority for this order</h2>
            <dl class="facts">
              <template v-for="a in doc.context.authority" :key="String(a.name)">
                <dt>{{ a.route }}</dt>
                <dd>{{ a.name }} — {{ a.workflow_state }}<template v-if="a.recommended_supplier || a.supplier">, awarded to {{ a.recommended_supplier || a.supplier }}</template></dd>
              </template>
            </dl>
          </section>
          <section v-else-if="doc.doctype === 'Purchase Order'" class="card pad warn-card">
            <h2 class="section-title">Authority for this order</h2>
            <p class="warn">Nothing authorises this order yet. Link an approved committee evaluation or waiver before sending it for approval.</p>
          </section>

          <section v-if="doc.context.bids?.length" class="card pad">
            <h2 class="section-title">Bids received</h2>
            <ul class="bids">
              <li v-for="b in doc.context.bids" :key="b.supplier_quotation">
                <span>{{ b.supplier }}</span>
                <span class="amount">{{ money(b.grand_total, b.currency) }}</span>
              </li>
            </ul>
          </section>

          <section v-if="doc.context.float && Object.keys(doc.context.float).length" class="card pad">
            <h2 class="section-title">The float</h2>
            <dl class="facts">
              <template v-if="doc.doctype === 'Employee Advance'">
                <dt>Paid out</dt><dd>{{ money(Number(doc.context.float.paid)) }}</dd>
                <dt>Accounted for</dt><dd>{{ money(Number(doc.context.float.claimed)) }}</dd>
                <dt>Returned</dt><dd>{{ money(Number(doc.context.float.returned)) }}</dd>
                <dt>Still to account for</dt><dd><strong>{{ money(Number(doc.context.float.balance)) }}</strong></dd>
              </template>
              <template v-else>
                <dt>Float</dt><dd>{{ doc.context.float.name }} — {{ doc.context.float.employee_name }}</dd>
                <dt>Paid out</dt><dd>{{ money(Number(doc.context.float.paid_amount)) }}</dd>
                <dt>State</dt><dd>{{ doc.context.float.workflow_state }}</dd>
              </template>
            </dl>
          </section>

          <section class="card pad">
            <h2 class="section-title">History</h2>
            <Timeline :entries="guide.timeline" />
            <p class="owner">Raised by {{ doc.owner_name }}</p>
          </section>
        </aside>
      </div>

      <ActSheet
        v-if="pending"
        :action="pending"
        :doc-name="doc.name"
        :saving="dirty"
        :busy="busy"
        :error="actResult && !actResult.ok ? actResult.error : null"
        :error-title="actResult?.title ?? null"
        :stale="!!actResult?.stale"
        @close="pending = null"
        @confirm="(reason) => pending && act(pending, reason)"
        @reload="reload"
      />
    </template>
  </div>
</template>

<style scoped>
.doc {
  display: grid;
  gap: 1.25rem;
}
.head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.4rem 1rem;
}
.back {
  grid-column: 1 / -1;
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  justify-self: start;
  color: var(--muted);
  font-size: var(--text-sm);
  text-decoration: none;
}
.titles {
  display: grid;
  gap: 0.35rem;
  min-width: 0;
}
.kicker {
  margin: 0;
  color: var(--brand);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}
h1 {
  font-size: var(--text-3xl);
  overflow-wrap: anywhere;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 0.75rem;
  margin-top: 0.2rem;
}
.step-of,
.chain {
  color: var(--faint);
  font-size: var(--text-sm);
}
.desk {
  align-self: start;
}
.layout {
  display: grid;
  gap: 1.25rem;
}
@media (min-width: 64rem) {
  .layout {
    grid-template-columns: minmax(0, 1fr) 22rem;
    align-items: start;
  }
  .aside {
    position: sticky;
    top: calc(var(--nav-height) + 1rem);
  }
}
.main,
.aside {
  display: grid;
  gap: 1.25rem;
  min-width: 0;
}
.pad {
  padding: 1.15rem;
}
.note {
  color: var(--muted);
}
.step {
  display: grid;
  gap: 1.1rem;
  border-color: color-mix(in srgb, var(--brand) 40%, var(--border));
  box-shadow: var(--shadow-card), inset 0 3px 0 var(--brand);
}
.step-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.25rem 0.75rem;
}
.step-head h2 {
  font-size: var(--text-xl);
}
.step-name {
  color: var(--muted);
}
.why {
  margin: 0;
  color: var(--muted);
  font-size: var(--text-sm);
}
.save-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.discard {
  justify-self: start;
}
.section-title {
  margin-bottom: 0.85rem;
  font-size: var(--text-base);
}
.who {
  margin: 0 0 0.5rem;
  color: var(--heading);
  font-weight: var(--weight-medium);
}
.approvers {
  display: grid;
  gap: 0.25rem;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: var(--text-sm);
}
.faint {
  color: var(--faint);
}
.warn {
  margin: 0.5rem 0 0;
  color: var(--warn);
  font-size: var(--text-sm);
}
.warn-card {
  border-color: color-mix(in srgb, var(--warn) 40%, var(--border));
}
.facts {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 0.45rem 0.9rem;
  margin: 0;
  font-size: var(--text-sm);
}
.facts dt {
  color: var(--faint);
}
.facts dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.bids {
  display: grid;
  gap: 0.4rem;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: var(--text-sm);
}
.bids li {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
}
.amount {
  font-variant-numeric: tabular-nums;
  color: var(--muted);
}
.owner {
  margin: 1rem 0 0;
  color: var(--faint);
  font-size: var(--text-xs);
}
</style>
