<script setup lang="ts">
// The only way server HTML reaches the page: through lib/html.sanitize. Messages keep their bold,
// line breaks and Desk links, and lose anything else -- see the reasoning in lib/html.ts.
import { computed } from "vue";

import { sanitize } from "../lib/html";

const props = withDefaults(defineProps<{ html: string | null | undefined; tag?: string }>(), { tag: "span" });
const clean = computed(() => sanitize(props.html));
</script>

<template>
  <component :is="tag" class="safe-html" v-html="clean" />
</template>
