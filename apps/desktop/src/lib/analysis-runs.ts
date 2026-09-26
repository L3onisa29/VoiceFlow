import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";
import type { AnalysisStatus } from "./analysis";
import { timeValue, type SchedulerStatus } from "./scheduler";
const id = z.number().int().positive();
const count = z.number().int().nonnegative();
const keySchema = z.object({ batchId: id, attemptId: id.nullable() });
const runSchema = z.object({
  key: keySchema,
  state: z.string(),
  createdAt: z.number().nullable(),
  startedAt: z.number().nullable(),
  endedAt: z.number().nullable(),
  elapsedMs: count.nullable(),
  sourceCount: count.nullable(),
  accepted: count.nullable(),
  rejected: count.nullable(),
  error: z.string().nullable(),
  inputBytes: count.nullable(),
});
const pageSchema = z.object({
  entries: z.array(runSchema),
  nextCursor: keySchema.nullable(),
});
const findingSchema = z.object({
  id,
  name: z.string(),
  text: z.string(),
  resultText: z.string(),
  reason: z.string(),
  action: z.enum(["keep", "remove", "replace"]),
  confidence: z.number().int().min(0).max(100).nullable(),
  outcome: z.string(),
  publication: id.nullable(),
  proposalId: id.nullable(),
  evidence: z.array(
    z.object({
      sessionId: id,
      revisionId: id.nullable(),
      originalIndex: count.nullable(),
    }),
  ),
});
const detailSchema = z.object({
  findings: z.array(findingSchema).default([]),
  run: runSchema,
  sources: z.array(
    z.object({
      sessionId: id,
      revisionId: id.nullable(),
      excluded: z.boolean(),
    }),
  ),
  proposals: z.array(
    z.object({
      proposalId: id.nullable(),
      relation: z.enum(["created", "updated", "existing"]),
      revision: id,
    }),
  ),
});
const invocationSchema = z.object({
  id,
  startedAt: z.number(),
  endedAt: z.number().nullable(),
  reason: z.string(),
  batchId: id.nullable(),
});
export type RunKey = z.infer<typeof keySchema>;
export type Run = z.infer<typeof runSchema>;
export type RunPage = z.infer<typeof pageSchema>;
export type RunDetail = z.infer<typeof detailSchema>;
export type Invocation = z.infer<typeof invocationSchema>;
export const runsApi = {
  page: async (before: RunKey | null) =>
    pageSchema.parse(await invoke("analysis_runs", { before, limit: 20 })),
  detail: async (key: RunKey) =>
    detailSchema.parse(
      await invoke("analysis_run_detail", { key: keySchema.parse(key) }),
    ),
  invocations: async () =>
    z.array(invocationSchema).parse(await invoke("scheduler_invocations")),
};
export const runStates: Record<string, string> = {
  prepared: "Preparata, nessun invio",
  running: "In corso",
  completed: "Completata",
  partial: "Parziale",
  failed: "Non riuscita",
  cancelled: "Annullata",
  interrupted: "Interrotta",
};
export const invocationReasons: Record<string, string> = {
  ...runStates,
  "daily-limit": "Limite giornaliero di richieste o durata raggiunto",
  "provider-unavailable": "Codex CLI o Node non disponibile",
  disabled: "Pianificazione disattivata",
  "outside-window": "Fuori dalla fascia oraria",
  backoff: "In attesa del retry o limite giornaliero dello scheduler",
  recording: "Dettatura in corso",
  "consent-unavailable": "Consenso OpenAI assente o non leggibile",
  "conditions-changed":
    "Fascia, consenso o disponibilità cambiati prima dell'avvio",
  "no-work":
    "Nessuna fonte preparabile, anche per esclusioni o limite di dimensione",
  "worker-error":
    "Errore del worker: controlla CLI, archivio e limiti giornalieri nelle Impostazioni",
};
export function schedulerReason(a: AnalysisStatus, s: SchedulerStatus) {
  if (s.workerRunning)
    return "Worker attivo. L'esito compare al termine del tentativo.";
  if (!a.settings.enabled)
    return "Analisi automatica non attiva: consenso OpenAI disattivato.";
  if (!s.schedule.enabled)
    return "Analisi automatica non attiva: pianificazione disattivata.";
  if (!s.helperAvailable)
    return "Helper non disponibile nel bundle. Installa la build completa.";
  if (!s.installed)
    return "LaunchAgent non installato. Ripara la pianificazione nelle Impostazioni.";
  if (!s.loaded)
    return "Job non caricato in launchd. Ripara la pianificazione nelle Impostazioni.";
  if (!s.matchesConfiguration)
    return "La configurazione installata non corrisponde alle preferenze. Ripara la pianificazione.";
  if (s.recording) return "Analisi sospesa durante la dettatura.";
  if (s.lastError) return s.lastError;
  if (s.nextAttempt > Date.now())
    return `Prossimo tentativo non prima di ${new Date(s.nextAttempt).toLocaleString("it-IT")}, entro la fascia configurata.`;
  return `Job pronto per la fascia ${timeValue(s.schedule.startMinute)}–${timeValue(s.schedule.endMinute)}. Un job caricato non prova che l'analisi sia riuscita.`;
}
