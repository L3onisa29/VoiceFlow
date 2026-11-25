import { AudioLines, Clock3, Cpu, Sparkles } from "lucide-react";
import { z } from "zod";

export const settingsSearchSchema = z.object({
  section: z
    .enum(["dictation", "analysis", "automation", "system"])
    .optional()
    .catch(undefined),
});

export const settingsSections = [
  {
    id: "dictation",
    heading: "text-settings",
    label: "Dettatura",
    icon: AudioLines,
    keywords:
      "lingua italiano inglese pulizia locale formato testo comandi paragrafi liste",
  },
  {
    id: "analysis",
    heading: "analysis-settings",
    label: "Analisi",
    icon: Sparkles,
    keywords:
      "openai codex consenso modello ragionamento correzioni approvazione confidenza limiti utilizzo budget fonti richieste",
  },
  {
    id: "automation",
    heading: "scheduler-settings",
    label: "Automazione",
    icon: Clock3,
    keywords: "pianificazione programmata fascia oraria orario diagnostica",
  },
  {
    id: "system",
    heading: "system-settings",
    label: "Sistema",
    icon: Cpu,
    keywords:
      "motore modello nemotron microfono accessibilità permessi scorciatoie tastiera audio diagnostica mac",
  },
] as const;
