import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";
const id = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
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
  origin: z.enum(["automatic", "manual"]).nullable().default(null),
  model: z.string().nullable().default(null),
  reasoningEffort: z.string().nullable().default(null),
  invocationId: id.nullable().default(null),
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
const spellingSchema = z.object({
  spelling: z.string(),
  intended: z.string(),
  sessionIds: z.array(id),
  confidence: z.number().int().min(0).max(100),
  realWord: z.boolean(),
  outcome: z.string(),
});
const rejectionSchema = z.object({
  kind: z.enum(["candidate", "misspelling"]).default("candidate"),
  position: id,
  code: z.string(),
  sources: z
    .array(z.object({ sessionId: id, revisionId: id.nullable() }))
    .default([]),
});
const detailSchema = z.object({
  findings: z.array(findingSchema).default([]),
  rejections: z.array(rejectionSchema).default([]),
  spellings: z.array(spellingSchema).default([]),
  attempts: z.array(runSchema),
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
      currentState: z.string().nullable(),
      currentRevision: id.nullable(),
    }),
  ),
});
export const invocationSchema = z.object({
  id,
  startedAt: z.number(),
  endedAt: z.number().nullable(),
  reason: z.string(),
  batchId: id.nullable(),
  attemptId: id.nullable().default(null),
  origin: z.enum(["automatic", "manual"]).nullable().default(null),
});
export type RunKey = z.infer<typeof keySchema>;
export type Run = z.infer<typeof runSchema>;
export type RunDetail = z.infer<typeof detailSchema>;
export type Invocation = z.infer<typeof invocationSchema>;
export const runsApi = {
  page: async (
    before: RunKey | null,
    filters: { state?: string; origin?: string } = {},
  ) =>
    pageSchema.parse(
      await invoke("analysis_runs", { before, limit: 20, filters }),
    ),
  detail: async (key: RunKey) =>
    detailSchema.parse(
      await invoke("analysis_run_detail", { key: keySchema.parse(key) }),
    ),
  runNow: async () => invocationSchema.parse(await invoke("scheduler_run_now")),
  invocations: async () =>
    z.array(invocationSchema).parse(await invoke("scheduler_invocations")),
};
export const runStates: Record<string, string> = {
  prepared: "Preparata, nessun invio",
  running: "In corso",
  completed: "Completata",
  partial: "Completata con avvisi",
  failed: "Non riuscita",
  cancelled: "Annullata",
  interrupted: "Interrotta",
};
export const invocationReasons: Record<string, string> = {
  ...runStates,
  requested: "Richiesta di avvio ricevuta",
  "worker-busy": "Un'analisi possiede già il worker. Attendi il suo esito.",
  "helper-unavailable":
    "Helper non disponibile. Apri la build completa di VoiceFlow.",
  "daily-limit": "Limite giornaliero di richieste o durata raggiunto",
  "provider-unavailable": "Codex CLI o Node non disponibile",
  disabled: "Pianificazione disattivata",
  "outside-window": "Fuori dalla fascia oraria",
  backoff:
    "In attesa dopo un tentativo non riuscito (5 minuti, al massimo 3 al giorno)",
  recording: "Dettatura in corso",
  "consent-unavailable": "Consenso OpenAI assente o non leggibile",
  "conditions-changed":
    "Fascia, consenso o disponibilità cambiati prima dell'avvio",
  "no-work":
    "Nessuna fonte preparabile, anche per esclusioni o limite di dimensione",
  "worker-error":
    "Errore del worker: controlla CLI, archivio e limiti giornalieri nelle Impostazioni",
};
export const spellingOutcomes: Record<string, string> = {
  observed: "Annotata",
  added: "Aggiunta al dizionario",
  proposed: "Proposta da confermare nei Suggerimenti",
  "already-active": "Già corretta dal dizionario o da una regola",
  "waiting-confidence": "In attesa · serve una segnalazione con certezza alta",
  "waiting-sessions":
    "In attesa · parola esistente, servono 5 sessioni concordi",
  "waiting-agreement":
    "In attesa · compare anche in sessioni dove non è stata segnalata",
  "waiting-terms": "In attesa · segnalata con termini diversi",
  "blocked-removed": "Non rientra · tolta a mano dal dizionario",
  "blocked-other-term": "Non rientra · è già variante di un altro termine",
  "blocked-conflict": "Non rientra · conflitto con dizionario o regole attive",
  "blocked-validation": "Non rientra · la sostituzione non supera i controlli",
};
// Codici stabili scritti dal validatore locale. Nessun testo del fornitore.
export const rejectionCodes: Record<string, string> = {
  "spelling-text": "Grafia non valida: serve un estratto di 1–4 parole",
  "spelling-intended": "Termine inteso non valido",
  schema: "Formato della proposta non valido",
  "confidence-missing": "Confidenza assente o fuori scala",
  "evidence-count": "Numero di riferimenti non ammesso",
  "evidence-reference": "Riferimento alla fonte non valido",
  "evidence-outside-batch": "Cita una fonte esterna al lotto",
  "batch-inactive": "Lotto non più attivo al momento del controllo",
  "source-unavailable": "Fonte citata non più disponibile",
  "source-changed": "Fonte cambiata o segmento non corrispondente",
  "session-missing": "Sessione citata inesistente",
  "revision-foreign": "Revisione non appartenente alla sessione",
  "revision-not-confirmed": "Revisione non ricordata come correzione",
  "asr-unavailable": "Originale ASR non disponibile per la sessione",
  "segment-missing": "Segmento originale inesistente",
  "finding-text": "Nome, estratto o motivo non valido",
  "action-rule-mismatch": "Azione e tipo di regola incoerenti",
  "excerpt-not-in-source": "Estratto non presente nella fonte citata",
  "keep-contradicts-correction":
    "Il mantenimento contraddice la correzione citata",
  "rule-no-effect": "La regola non modifica l'estratto",
  "rule-changes-context": "La regola modifica le parole attorno all'estratto",
  "example-not-grounded": "Nessun esempio positivo ricavato dalla fonte",
  "result-contradicts-correction":
    "Il risultato non compare nella correzione citata",
  "evidence-lacks-example": "La fonte non contiene alcun esempio positivo",
  "example-mismatch": "La regola non produce l'esito dichiarato negli esempi",
  "examples-count": "Numero di esempi non ammesso",
  "example-text": "Esempio vuoto, troppo lungo o non valido",
  "example-markers": "Marcatori non ammessi negli esempi",
  "negative-example-changed": "Una frase di controllo risulta modificata",
  "positive-example-unchanged": "Un esempio positivo non mostra modifiche",
  "variants-count": "Numero di varianti non ammesso",
  "reason-text": "Motivo della regola non valido",
  language: "Lingua non valida",
  groups: "Gruppi non più disponibili",
  "rule-invalid": "Definizione non accettata dal compilatore locale",
};
/** Badge variant for a run or attempt state. */
export function runStateVariant(state: string) {
  switch (state) {
    case "completed":
      return "success" as const;
    case "partial":
      return "warning" as const;
    case "failed":
    case "interrupted":
      return "destructive" as const;
    case "running":
      return "secondary" as const;
    default:
      return "outline" as const;
  }
}
