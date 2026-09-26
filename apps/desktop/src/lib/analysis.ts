import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";

export const analysisSettingsSchema = z.object({
  enabled: z.boolean(),
  autoApproveHighConfidence: z.boolean().default(false),
  maxSources: z.number().int().min(1).max(100),
  maxInputBytes: z.number().int().min(4096).max(262144),
  maxRequestsPerDay: z.number().int().min(1).max(20),
  requestTimeoutSeconds: z.number().int().min(1).max(600),
  maxSecondsPerDay: z.number().int().min(1).max(3600),
});
const count = z.number().int().nonnegative();
const statusSchema = z.object({
  settings: analysisSettingsSchema,
  requestsToday: count,
  inputBytesToday: count,
  elapsedSecondsToday: count,
  reservedSecondsToday: count,
  pendingSources: count,
  excludedSessions: count,
  lastState: z.string().nullable(),
  lastError: z.string().nullable(),
  acceptedCandidates: count,
  rejectedCandidates: count,
});
export type AnalysisSettings = z.infer<typeof analysisSettingsSchema>;
export type AnalysisStatus = z.infer<typeof statusSchema>;
export const analysisApi = {
  status: async () => statusSchema.parse(await invoke("analysis_status")),
  configure: async (settings: AnalysisSettings, consent: boolean) =>
    statusSchema.parse(
      await invoke("analysis_configure", {
        settings: analysisSettingsSchema.parse(settings),
        consent,
      }),
    ),
  excluded: async (id: number) =>
    z.boolean().parse(await invoke("analysis_excluded", { id })),
  exclude: (id: number, excluded: boolean) =>
    invoke<void>("analysis_exclude", { id, excluded }),
};
export const analysisErrors: Record<string, string> = {
  "ingest-storage":
    "Errore di archiviazione. Le proposte già salvate restano disponibili; il lotto può essere riprovato.",
  "provider-unavailable":
    "Codex CLI o Node non disponibile. Verifica l'installazione.",
  "provider-exit":
    "Codex CLI non ha completato la richiesta. Verifica rete e accesso a Codex.",
  "provider-io": "Comunicazione con Codex CLI interrotta.",
  "invalid-output":
    "Risposta incompleta o non valida. Nessuna proposta importata.",
  "output-limit": "La risposta ha superato il limite di dimensione.",
  "candidate-rejected":
    "Alcune proposte non hanno superato la validazione. Le altre sono disponibili nei suggerimenti.",
  timeout: "Tempo massimo raggiunto. Nessuna nuova regola attivata.",
  cancelled: "Analisi interrotta.",
  "privacy-changed":
    "Lotto annullato dopo una modifica al consenso o alle esclusioni.",
  "source-changed":
    "Le sorgenti del lotto sono state eliminate o modificate. Il worker preparerà un nuovo lotto.",
  interrupted:
    "Esecuzione interrotta. Il worker potrà riprendere entro i limiti impostati.",
};
