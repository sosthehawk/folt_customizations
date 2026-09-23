// Light, dark, or whatever the device is doing.
//
// The resolved theme lives on `<html data-theme>`, and www/folt.html has already set it once
// before first paint -- this module owns it from there on. The preference and the resolution are
// deliberately two different things: "system" is a preference, and it resolves to light or dark
// depending on what the device says right now, which can change under a running tab at sunset.

import { ref } from "vue";

export type ThemeChoice = "light" | "dark" | "system";
export type Resolved = "light" | "dark";

/** Must match the key the inline script in www/folt.html reads. */
const KEY = "folt-theme";

const QUERY = "(prefers-color-scheme: dark)";

function stored(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY);
    if (value === "light" || value === "dark" || value === "system") return value;
  } catch {
    // Private windows and blocked site data both throw on read. A preference we cannot read is
    // the same as one that was never set.
  }
  return "system";
}

function systemIsDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(QUERY).matches;
}

export const choice = ref<ThemeChoice>(stored());
export const resolved = ref<Resolved>(
  choice.value === "system" ? (systemIsDark() ? "dark" : "light") : choice.value,
);

function paint() {
  resolved.value =
    choice.value === "system" ? (systemIsDark() ? "dark" : "light") : choice.value;
  document.documentElement.setAttribute("data-theme", resolved.value);
}

export function setTheme(next: ThemeChoice) {
  choice.value = next;
  try {
    // "system" is REMOVED rather than stored, so a device that later changes its mind is followed
    // instead of being pinned to whatever it happened to be on the day somebody chose it.
    if (next === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, next);
  } catch {
    // The choice still applies to this tab; it just will not survive a reload.
  }
  paint();
}

/** Start following the device, for as long as the preference is "system". */
export function watchSystem() {
  if (typeof window.matchMedia !== "function") return;
  const media = window.matchMedia(QUERY);
  const react = () => {
    if (choice.value === "system") paint();
  };
  // addEventListener is not available on MediaQueryList in older WebKit, where addListener is.
  // The phones this is built for are not all new.
  if (typeof media.addEventListener === "function") media.addEventListener("change", react);
  else if (typeof media.addListener === "function") media.addListener(react);

  paint();
}
