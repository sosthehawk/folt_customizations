<script setup lang="ts">
// Every notification, newest first. A phone has no room for the bell's panel, so this is where
// the bell tab goes.
import { onMounted } from "vue";
import { useRouter } from "vue-router";

import EmptyState from "../components/EmptyState.vue";
import SafeHtml from "../components/SafeHtml.vue";
import SkeletonList from "../components/SkeletonList.vue";
import { docPath } from "../lib/doctypes";
import { when } from "../lib/format";
import { clearRead, loadBell, markRead, store } from "../lib/store";
import type { Notice } from "../lib/types";

const router = useRouter();

async function open(notice: Notice) {
  if (!notice.read) await markRead(notice.name);
  if (notice.document_type && notice.document_name) router.push(docPath(notice.document_type, notice.document_name));
}

onMounted(() => void loadBell());
</script>

<template>
  <div class="page">
    <header class="head">
      <h1>Notifications</h1>
      <div class="tools">
        <button v-if="store.bell.data.unread" type="button" class="btn btn-quiet" @click="markRead()">Mark all read</button>
        <button v-if="store.bell.data.logs.some((l) => l.read)" type="button" class="btn btn-quiet" @click="clearRead()">Clear read</button>
      </div>
    </header>
    <SkeletonList v-if="!store.bell.loaded" :rows="4" height="4rem" />
    <EmptyState v-else-if="!store.bell.data.logs.length" icon="bell" title="No notifications" hint="When a document reaches a step you hold, you will hear about it here." />
    <ul v-else class="list card">
      <li v-for="notice in store.bell.data.logs" :key="notice.name">
        <button type="button" class="notice" :class="{ unread: !notice.read }" @click="open(notice)">
          <span class="pip" :class="{ on: !notice.read }" aria-hidden="true" />
          <span class="text">
            <SafeHtml :html="notice.subject" class="subject" />
            <SafeHtml v-if="notice.email_content" :html="notice.email_content" tag="span" class="body" />
            <span class="meta"><template v-if="notice.from_user_name">{{ notice.from_user_name }} · </template>{{ when(notice.creation) }}</span>
          </span>
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.page {
  display: grid;
  gap: 1.25rem;
}
.head {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 0.5rem;
}
h1 {
  font-size: var(--text-3xl);
}
.tools {
  display: flex;
  gap: 0.25rem;
}
.list {
  margin: 0;
  padding: 0;
  list-style: none;
  overflow: hidden;
}
.list li + li {
  border-top: 1px solid var(--border);
}
.notice {
  display: flex;
  gap: 0.75rem;
  width: 100%;
  padding: 0.9rem 1rem;
  border: 0;
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
  width: 9px;
  height: 9px;
  margin-top: 0.45rem;
  border-radius: 50%;
}
.pip.on {
  background: var(--brand);
}
.text {
  display: grid;
  gap: 0.2rem;
  min-width: 0;
}
.subject {
  color: var(--heading);
  font-weight: var(--weight-medium);
}
.body {
  color: var(--muted);
  font-size: var(--text-sm);
}
.meta {
  color: var(--faint);
  font-size: var(--text-xs);
}
</style>
