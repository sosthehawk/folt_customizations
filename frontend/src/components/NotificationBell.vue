<script setup lang="ts">
// The bell, and the panel behind it. The unread count is asked of the server every time rather
// than kept by arithmetic as the Desk does: a colleague approving in the Desk changes this number,
// and the only way to be right about that is to ask.

import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { docPath } from "../lib/doctypes";
import { when } from "../lib/format";
import { plain } from "../lib/html";
import { clearRead, loadBell, markRead, store } from "../lib/store";
import type { Notice } from "../lib/types";
import Icon from "./Icon.vue";

const router = useRouter();
const open = ref(false);
const root = ref<HTMLElement | null>(null);

const unread = computed(() => store.bell.data.unread);
const logs = computed(() => store.bell.data.logs.slice(0, 8));
const hasRead = computed(() => store.bell.data.logs.some((log) => log.read === 1));

function onDocumentClick(event: MouseEvent) {
  if (open.value && root.value && !root.value.contains(event.target as Node)) open.value = false;
}
function onEscape(event: KeyboardEvent) {
  if (event.key === "Escape") open.value = false;
}
onMounted(() => {
  document.addEventListener("click", onDocumentClick);
  document.addEventListener("keydown", onEscape);
});
onBeforeUnmount(() => {
  document.removeEventListener("click", onDocumentClick);
  document.removeEventListener("keydown", onEscape);
});

async function toggle() {
  open.value = !open.value;
  if (open.value) await loadBell();
}

/** Open the document a notice is about. FoLT's bell `link` is a Desk URL, so the route is built
 *  from document_type/document_name instead -- a client-side move, not a reload into the Desk. */
async function openNotice(notice: Notice) {
  open.value = false;
  if (!notice.read) await markRead(notice.name);
  if (notice.document_type && notice.document_name) router.push(docPath(notice.document_type, notice.document_name));
}
</script>

<template>
  <div ref="root" class="bell">
    <button
      type="button"
      class="trigger"
      :aria-expanded="open"
      :aria-label="unread ? `Notifications, ${unread} unread` : 'Notifications'"
      @click="toggle"
    >
      <Icon name="bell" :size="18" />
      <span v-if="unread" class="count">{{ unread > 99 ? "99+" : unread }}</span>
    </button>

    <div v-if="open" class="panel" role="dialog" aria-label="Notifications">
      <header class="head">
        <strong>Notifications</strong>
        <div class="tools">
          <button v-if="unread" type="button" class="link" @click="markRead()">Mark all read</button>
          <button v-if="hasRead" type="button" class="link" @click="clearRead()">Clear read</button>
        </div>
      </header>
      <p v-if="!logs.length" class="none">Nothing yet. Anything waiting on you will appear here.</p>
      <ul v-else class="list">
        <li v-for="notice in logs" :key="notice.name">
          <button type="button" class="notice" :class="{ unread: !notice.read }" @click="openNotice(notice)">
            <span v-if="!notice.read" class="pip" aria-hidden="true" />
            <span class="text">
              <span class="subject">{{ plain(notice.subject) }}</span>
              <span class="meta">
                <template v-if="notice.from_user_name">{{ notice.from_user_name }} · </template>{{ when(notice.creation) }}
              </span>
            </span>
          </button>
        </li>
      </ul>
      <RouterLink :to="{ name: 'notifications' }" class="all" @click="open = false">See all</RouterLink>
    </div>
  </div>
</template>

<style scoped>
.bell {
  position: relative;
}
.trigger {
  position: relative;
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--muted);
  cursor: pointer;
}
.trigger:hover {
  color: var(--brand);
  border-color: var(--border-strong);
}
.count {
  position: absolute;
  top: -6px;
  right: -6px;
  min-width: 18px;
  padding: 0 4px;
  border-radius: var(--radius-pill);
  background: var(--brand);
  color: var(--action-fg);
  font-size: 11px;
  font-weight: 700;
  line-height: 18px;
  font-variant-numeric: tabular-nums;
}
.panel {
  position: absolute;
  top: calc(100% + 0.5rem);
  right: 0;
  z-index: 40;
  width: min(23rem, calc(100vw - 2rem));
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface);
  box-shadow: var(--shadow-lift);
  overflow: hidden;
}
.head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.8rem 1rem;
  border-bottom: 1px solid var(--border);
  font-size: var(--text-sm);
}
.tools {
  display: flex;
  gap: 0.75rem;
  margin-left: auto;
}
.link {
  border: 0;
  background: none;
  color: var(--brand);
  font-size: var(--text-xs);
  cursor: pointer;
  padding: 0;
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 24rem;
  overflow-y: auto;
}
.notice {
  display: flex;
  gap: 0.6rem;
  width: 100%;
  padding: 0.75rem 1rem;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: none;
  text-align: left;
  cursor: pointer;
}
.notice:hover {
  background: var(--surface-sunk);
}
.notice.unread {
  background: var(--brand-tint);
}
.pip {
  flex: none;
  width: 8px;
  height: 8px;
  margin-top: 0.45rem;
  border-radius: 50%;
  background: var(--brand);
}
.text {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.subject {
  font-size: var(--text-sm);
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.meta {
  color: var(--faint);
  font-size: var(--text-xs);
}
.none {
  margin: 0;
  padding: 1.5rem 1rem;
  color: var(--muted);
  font-size: var(--text-sm);
  text-align: center;
}
.all {
  display: block;
  padding: 0.7rem;
  color: var(--brand);
  font-size: var(--text-sm);
  text-align: center;
  text-decoration: none;
}
</style>
