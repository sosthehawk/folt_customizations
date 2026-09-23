<script setup lang="ts">
// A top bar on a wide screen, a bottom tab bar on a phone. NOT a hamburger: the thing people open
// this for -- what is waiting on them -- is one of four destinations, and hiding four items behind
// a menu trades the point of the app for a strip of whitespace. A bottom bar is where a thumb is.
//
// The bottom bar is the page's one frosted surface. backdrop-filter is a per-frame GPU cost and
// creates a containing block, so it goes on a single fixed element and never on a repeated row --
// the constraint folt_desk.css states for the Desk's guided layer, applied here too.

import { computed } from "vue";

import { boot } from "../lib/boot";
import { store } from "../lib/store";
import Icon from "./Icon.vue";
import NotificationBell from "./NotificationBell.vue";
import ThemeToggle from "./ThemeToggle.vue";

// A string, not a literal src: Vue's SFC compiler turns a literal into an import and rollup would
// bundle a hashed second copy of a file the Desk, login page and favicon already share.
const EMBLEM = "/assets/folt_customizations/images/folt-emblem.svg";

const awaiting = computed(() => store.counts.awaiting ?? 0);

const items = computed(() => [
  { name: "tasks", label: "My Tasks", icon: "tasks", badge: awaiting.value },
  { name: "workflows", label: "Workflows", icon: "flows", badge: 0 },
  { name: "new", label: "New", icon: "plus", badge: 0 },
]);
</script>

<template>
  <header class="bar">
    <div class="inner">
      <RouterLink :to="{ name: 'tasks' }" class="brand" aria-label="FoLT — My Tasks">
        <img :src="EMBLEM" alt="" aria-hidden="true" class="mark" width="26" height="26" />
        <span class="word">FoLT</span>
      </RouterLink>

      <nav class="wide" aria-label="Main">
        <RouterLink v-for="item in items" :key="item.name" :to="{ name: item.name }" class="tab">
          <Icon :name="item.icon" :size="17" />
          {{ item.label }}
          <span v-if="item.badge" class="count">{{ item.badge }}</span>
        </RouterLink>
      </nav>

      <div class="right">
        <ThemeToggle />
        <NotificationBell />
        <span class="who" :title="boot.user">{{ boot.full_name }}</span>
        <a class="desk" href="/desk/folt-tasks" title="Open the Desk">
          Desk <Icon name="external" :size="14" />
        </a>
      </div>
    </div>
  </header>

  <nav class="tabs" aria-label="Main">
    <RouterLink v-for="item in items" :key="item.name" :to="{ name: item.name }" class="tabitem">
      <Icon :name="item.icon" :size="22" />
      <span class="label">{{ item.label }}</span>
      <span v-if="item.badge" class="dot">{{ item.badge }}</span>
    </RouterLink>
    <RouterLink :to="{ name: 'notifications' }" class="tabitem">
      <Icon name="bell" :size="22" />
      <span class="label">Alerts</span>
      <span v-if="store.bell.data.unread" class="dot">{{ store.bell.data.unread }}</span>
    </RouterLink>
  </nav>
</template>

<style scoped>
.bar {
  position: sticky;
  top: 0;
  z-index: 20;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}
.inner {
  display: flex;
  align-items: center;
  gap: 1.25rem;
  max-width: 76rem;
  height: var(--nav-height);
  margin: 0 auto;
  padding: 0 1rem;
}
.brand {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: var(--heading);
  text-decoration: none;
  flex: none;
}
.mark {
  display: block;
  width: 26px;
  height: 26px;
}
.word {
  font-size: var(--text-lg);
  font-weight: 700;
  letter-spacing: -0.01em;
}
.wide {
  display: flex;
  gap: 0.25rem;
}
.tab {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  padding: 0.45rem 0.8rem;
  border-radius: var(--radius-md);
  color: var(--muted);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  text-decoration: none;
  transition: background-color var(--dur-control) ease, color var(--dur-control) ease;
}
.tab:hover {
  background: var(--surface-sunk);
  color: var(--fg);
}
.tab.router-link-exact-active,
.tab.router-link-active:not([href="/folt/"]) {
  background: var(--brand-tint-strong);
  color: var(--brand);
}
.count {
  min-width: 1.25rem;
  padding: 0 0.35rem;
  border-radius: var(--radius-pill);
  background: var(--brand);
  color: var(--action-fg);
  font-size: var(--text-xs);
  font-weight: 700;
  line-height: 1.25rem;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.right {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-left: auto;
}
.who {
  color: var(--muted);
  font-size: var(--text-sm);
  max-width: 11rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.desk {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  color: var(--muted);
  font-size: var(--text-sm);
  text-decoration: none;
}
.desk:hover {
  color: var(--brand);
}
.tabs {
  display: none;
}

@media (max-width: 48rem) {
  .wide,
  .who,
  .desk {
    display: none;
  }
  .right {
    gap: 0.5rem;
  }
  .tabs {
    position: fixed;
    right: 0;
    bottom: 0;
    left: 0;
    z-index: 30;
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    border-top: 1px solid var(--glass-rim);
    background: var(--glass);
    -webkit-backdrop-filter: blur(20px) saturate(150%);
    backdrop-filter: blur(20px) saturate(150%);
    padding-bottom: env(safe-area-inset-bottom, 0);
  }
  .tabitem {
    position: relative;
    display: grid;
    place-items: center;
    gap: 2px;
    min-height: 56px;
    padding: 0.4rem 0.25rem;
    color: var(--muted);
    text-decoration: none;
  }
  .tabitem.router-link-exact-active,
  .tabitem.router-link-active:not([href="/folt/"]) {
    color: var(--brand);
  }
  .label {
    font-size: 12px;
    font-weight: var(--weight-medium);
  }
  .dot {
    position: absolute;
    top: 0.3rem;
    left: 50%;
    margin-left: 0.5rem;
    min-width: 1.1rem;
    padding: 0 0.3rem;
    border-radius: var(--radius-pill);
    background: var(--brand);
    color: var(--action-fg);
    font-size: 11px;
    font-weight: 700;
    line-height: 1.1rem;
    text-align: center;
  }
}
</style>
