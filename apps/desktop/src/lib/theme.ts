import { useSyncExternalStore } from "react";

export type ResolvedTheme = "light" | "dark";

const query = "(prefers-color-scheme: dark)";
const listeners = new Set<() => void>();

function apply(theme: ResolvedTheme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
  for (const listener of listeners) listener();
}

/** Follows the macOS appearance: toggles `.dark` on <html> from the system preference. */
export function initTheme() {
  const media = window.matchMedia(query);
  apply(media.matches ? "dark" : "light");
  media.addEventListener("change", (event) =>
    apply(event.matches ? "dark" : "light"),
  );
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function snapshot(): ResolvedTheme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

export function useResolvedTheme(): ResolvedTheme {
  return useSyncExternalStore(subscribe, snapshot, () => "light");
}
