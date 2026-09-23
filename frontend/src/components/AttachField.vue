<script setup lang="ts">
// An attachment slot: shows what is attached, and -- where the step allows it -- takes a file.
// The upload goes to spa.attach, which checks the step, the custodian and allow_on_submit BEFORE it
// stores anything, then sets the field in the same transaction.
import { ref } from "vue";

import Icon from "./Icon.vue";

const props = defineProps<{ id: string; url: string | null; editable: boolean; busy?: boolean }>();
const emit = defineEmits<{ upload: [File] }>();
const input = ref<HTMLInputElement | null>(null);

function fileName(url: string) {
  return decodeURIComponent(url.split("/").pop() ?? url);
}

function picked(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (file) emit("upload", file);
  if (input.value) input.value.value = "";
}
</script>

<template>
  <div class="attach">
    <a v-if="props.url" :href="props.url" target="_blank" rel="noopener" class="file">
      <Icon name="file" :size="18" /> <span>{{ fileName(props.url) }}</span>
    </a>
    <span v-else class="none">Nothing attached</span>
    <template v-if="editable">
      <input :id="id" ref="input" type="file" class="sr-only" @change="picked" />
      <label :for="id" class="btn btn-secondary pick" :class="{ busy }">
        <Icon name="clip" :size="16" /> {{ busy ? "Uploading…" : props.url ? "Replace" : "Attach a file" }}
      </label>
    </template>
  </div>
</template>

<style scoped>
.attach {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
}
.file {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  min-width: 0;
  color: var(--brand);
  font-size: var(--text-sm);
  text-decoration: none;
  overflow-wrap: anywhere;
}
.file:hover span {
  text-decoration: underline;
}
.none {
  color: var(--faint);
  font-size: var(--text-sm);
}
.pick {
  min-height: 38px;
}
.pick.busy {
  pointer-events: none;
  opacity: 0.6;
}
input:focus-visible + .pick {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}
</style>
