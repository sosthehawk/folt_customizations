<script setup lang="ts">
// Light · Match device · Dark, as three segments rather than a cycling button: a button that
// cycles cannot say what it will do next, and three states take up to two presses to reach.
import { choice, setTheme, type ThemeChoice } from "../lib/theme";
import Icon from "./Icon.vue";

const OPTIONS: { value: ThemeChoice; icon: string; label: string }[] = [
  { value: "light", icon: "sun", label: "Light" },
  { value: "system", icon: "half", label: "Match device" },
  { value: "dark", icon: "moon", label: "Dark" },
];
</script>

<template>
  <div class="toggle" role="radiogroup" aria-label="Colour theme">
    <button
      v-for="option in OPTIONS"
      :key="option.value"
      type="button"
      role="radio"
      class="seg"
      :class="{ on: choice === option.value }"
      :aria-checked="choice === option.value"
      :title="option.label"
      :aria-label="option.label"
      @click="setTheme(option.value)"
    >
      <Icon :name="option.icon" :size="15" />
    </button>
  </div>
</template>

<style scoped>
.toggle {
  display: inline-flex;
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: var(--radius-pill);
  background: var(--surface-sunk);
}
.seg {
  display: grid;
  place-items: center;
  width: 30px;
  height: 26px;
  border: 0;
  border-radius: var(--radius-pill);
  background: none;
  color: var(--faint);
  cursor: pointer;
  transition: color var(--dur-control) ease, background-color var(--dur-control) ease;
}
.seg:hover {
  color: var(--fg);
}
.seg.on {
  background: var(--surface);
  color: var(--brand);
  box-shadow: var(--shadow-card);
}
</style>
