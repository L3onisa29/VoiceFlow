import { BookOpen, Mic, Settings2, Lightbulb } from "lucide-react";

export const primaryNavigation = [
  { to: "/", label: "Dettatura", icon: Mic },
  { to: "/dictionary", label: "Dizionario", icon: BookOpen },
  { to: "/suggestions", label: "Suggerimenti", icon: Lightbulb },
] as const;

export const settingsNavigation = {
  to: "/settings",
  label: "Impostazioni",
  icon: Settings2,
} as const;
