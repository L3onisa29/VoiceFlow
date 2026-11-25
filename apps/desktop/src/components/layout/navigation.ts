import {
  AudioWaveform,
  BookOpen,
  History,
  ListChecks,
  Mic,
  Settings2,
} from "lucide-react";

// Order matters: ⌘1…⌘5 follow this list.
export const primaryNavigation = [
  { to: "/", label: "Dettatura", icon: Mic },
  { to: "/history", label: "Storico", icon: History },
  { to: "/dictionary", label: "Dizionario", icon: BookOpen },
  { to: "/review", label: "Revisione", icon: ListChecks },
  { to: "/voice-profile", label: "Profilo vocale", icon: AudioWaveform },
] as const;

export const settingsNavigation = {
  to: "/settings",
  label: "Impostazioni",
  icon: Settings2,
} as const;

/** Nested pages keep their section active (e.g. /review/runs/2 → /review). */
export function isNavigationActive(pathname: string, to: string) {
  if (to === "/") return pathname === "/";
  return pathname === to || pathname.startsWith(`${to}/`);
}
