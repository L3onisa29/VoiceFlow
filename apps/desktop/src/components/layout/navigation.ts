import { BookOpen, Mic, Settings2 } from "lucide-react";

export const primaryNavigation = [
  { to: "/", label: "Dettatura", icon: Mic },
  { to: "/dictionary", label: "Dizionario", icon: BookOpen },
] as const;

export const settingsNavigation = {
  to: "/settings",
  label: "Impostazioni",
  icon: Settings2,
} as const;
