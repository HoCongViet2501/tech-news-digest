"use client";

import { useSyncExternalStore } from "react";

import { THEME_KEY, type ThemeChoice } from "@/lib/theme";

const EVENT = "themechange";
const ORDER: ThemeChoice[] = ["system", "dark", "light"];
const LABEL: Record<ThemeChoice, string> = { system: "Theme: system", dark: "Theme: dark", light: "Theme: light" };
const ICON: Record<ThemeChoice, string> = { system: "◐", dark: "☾", light: "☀" };

function read(): ThemeChoice {
  const t = document.documentElement.dataset.theme;
  return t === "dark" || t === "light" ? t : "system";
}

function subscribe(onChange: () => void) {
  window.addEventListener(EVENT, onChange);
  return () => window.removeEventListener(EVENT, onChange);
}

/** Cycles system → dark → light. "system" follows prefers-color-scheme (see globals.css). */
export function ThemeToggle() {
  const choice = useSyncExternalStore(subscribe, read, () => "system" as const);

  function next() {
    const value = ORDER[(ORDER.indexOf(choice) + 1) % ORDER.length];
    const root = document.documentElement;
    try {
      if (value === "system") localStorage.removeItem(THEME_KEY);
      else localStorage.setItem(THEME_KEY, value);
    } catch {
      // Storage blocked: the choice still applies to this page view.
    }
    if (value === "system") delete root.dataset.theme;
    else root.dataset.theme = value;
    window.dispatchEvent(new Event(EVENT));
  }

  return (
    <button
      type="button"
      onClick={next}
      aria-label={`${LABEL[choice]}. Click to change.`}
      title={LABEL[choice]}
      className="flex size-9 items-center justify-center rounded-full text-lg hover:bg-zinc-100 dark:hover:bg-zinc-800"
    >
      <span aria-hidden="true">{ICON[choice]}</span>
    </button>
  );
}
