import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";
const id = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
const context = z.object({
  before: z.string().nullable(),
  after: z.string().nullable(),
  exceptBefore: z.array(z.string()),
  exceptAfter: z.array(z.string()),
});
const example = z.object({ input: z.string(), output: z.string() });
const operation = z.discriminatedUnion("kind", [
  z.object({
    kind: z.literal("remove-false-start"),
    source: z.array(z.string()),
  }),
  z.object({ kind: z.literal("repeat"), word: z.string() }),
  z.object({
    kind: z.literal("repeat-phrase"),
    phrase: z.array(z.string()).min(2).max(3),
  }),
  z.object({
    kind: z.literal("elision"),
    first: z.string(),
    prefix: z.string(),
  }),
]);
export const candidateSchema = z.object({
  definition: z.discriminatedUnion("category", [
    z.object({
      category: z.literal("terminology"),
      preferred: z.string(),
      variants: z.array(z.string()),
      matching: z.enum(["exact", "unicode-lowercase"]),
      context,
    }),
    z.object({ category: z.literal("cleanup"), operation, context }),
  ]),
  language: z.enum(["*", "it", "en"]),
  groupIds: z.array(id),
  reason: z.string(),
  positiveExamples: z.array(example).min(1).max(16),
  negativeExamples: z.array(example).min(1).max(16),
});
const proposalSchema = z.object({
  id,
  revision: id,
  candidate: candidateSchema,
  state: z.enum(["proposed", "approved", "rejected", "deferred", "disabled"]),
  rejectionReason: z.string().nullable(),
  approvedAt: z.number().nullable(),
  confidence: z.number().int().min(0).max(100).nullable().default(null),
  analysisOutcome: z.string().nullable().default(null),
  conflicts: z.array(z.string()),
  evidence: z.array(
    z.object({
      sessionId: id,
      revisionId: id.nullable(),
      originalIndex: z.number().int().nonnegative().nullable(),
      before: z.string(),
      after: z.string().nullable(),
      humanConfirmed: z.boolean(),
    }),
  ),
});
export const reviewSchema = z.object({
  proposals: z.array(proposalSchema),
  version: z.number().int().nonnegative(),
  dictionaryVersion: id,
  groups: z.array(z.object({ id, name: z.string() })),
  publications: z.array(
    z.object({
      version: id,
      previousVersion: z.number().nullable(),
      action: z.string(),
      createdAt: z.number(),
      proposalIds: z.array(id),
    }),
  ),
});
const count = z.number().int().nonnegative();
const ruleReplaySchema = z.object({
  proposalId: id,
  active: z.boolean(),
  changedSessions: count.nullable(),
  excerpts: z.array(
    z.object({ sessionId: id, before: z.string(), after: z.string() }),
  ),
  error: z.string().nullable(),
  atPublication: z
    .object({ publication: id, changedSessions: count, sessions: count })
    .nullable(),
  usage: z
    .object({
      activations: count,
      sessionsWithActivation: count,
      trackedSessions: count,
    })
    .nullable(),
});
const replaySchema = z.object({
  sessions: count,
  version: count,
  rules: z.array(ruleReplaySchema),
});
export type RuleReplay = z.infer<typeof ruleReplaySchema>;
export type Candidate = z.infer<typeof candidateSchema>;
export type Proposal = z.infer<typeof proposalSchema>;
export type ReviewSnapshot = z.infer<typeof reviewSchema>;
export type ReviewAction =
  | { kind: "approve" | "defer" | "disable" }
  | { kind: "reject"; reason: string }
  | { kind: "edit"; candidate: Candidate }
  | { kind: "restore"; version: number };
export type ReviewRequest = {
  id: number | null;
  expectedRevision: number | null;
  expectedVersion: number;
  dictionaryVersion: number;
  requestKey: string;
  action: ReviewAction;
};
export const suggestionsApi = {
  read: async () => reviewSchema.parse(await invoke("suggestions_snapshot")),
  review: async (request: ReviewRequest) =>
    reviewSchema.parse(await invoke("suggestions_review", { request })),
  /** Riprova locale delle regole sugli originali salvati. Nessuna scrittura. */
  replay: async () => replaySchema.parse(await invoke("suggestions_replay")),
};
const sessionsLabel = (n: number) => (n === 1 ? "1 sessione" : `${n} sessioni`);
export function replayHeadline(rule: RuleReplay, sessions: number) {
  if (rule.changedSessions === null)
    return "Riprova sullo storico non riuscita: la regola non si combina con quelle pubblicate.";
  if (rule.changedSessions === 0)
    return `Riprova sullo storico: non cambia nessuna sessione salvata (${sessions} riprovate).`;
  return `Riprova sullo storico: cambia ${sessionsLabel(rule.changedSessions)} su ${sessions}.`;
}
export function usageHeadline(usage: NonNullable<RuleReplay["usage"]>) {
  if (usage.trackedSessions === 0)
    return "Attivazioni: nessuna sessione registrata dopo l'approvazione con il contatore attivo.";
  if (usage.activations === 0)
    return `Attivazioni: mai scattata in ${sessionsLabel(usage.trackedSessions)} registrate dopo l'approvazione.`;
  return `Attivazioni: ${usage.activations} in ${sessionsLabel(usage.sessionsWithActivation)} su ${usage.trackedSessions} registrate dopo l'approvazione.`;
}
export const stateLabels = {
  proposed: "Da valutare",
  approved: "Approvata · attiva",
  rejected: "Rifiutata",
  deferred: "Rimandata",
  disabled: "Approvata · disattivata",
} as const;

// UI helpers for the review queue. No IPC.
export const languageNames = {
  "*": "Tutte le lingue",
  it: "Italiano",
  en: "Inglese",
} as const;
export const proposalKind = (candidate: Candidate) =>
  candidate.definition.category === "terminology" ? "Terminologia" : "Pulizia";
export function proposalTitle(candidate: Candidate) {
  const definition = candidate.definition;
  if (definition.category === "terminology") return definition.preferred;
  switch (definition.operation.kind) {
    case "remove-false-start":
      return "Falsa partenza";
    case "repeat":
      return "Ripetizione di parola";
    case "repeat-phrase":
      return "Ripetizione di frase";
    case "elision":
      return "Elisione";
  }
}
/** One sentence describing what the rule does. */
export function proposalRule(candidate: Candidate) {
  const definition = candidate.definition;
  if (definition.category === "terminology")
    return `Varianti: ${definition.variants.join(", ")}. ${definition.matching === "exact" ? "Corrispondenza esatta." : "Maiuscole e minuscole equivalenti."}`;
  const operation = definition.operation;
  switch (operation.kind) {
    case "remove-false-start":
      return `Elimina solo la prima parola di “${operation.source.join(" ")}”.`;
    case "repeat":
      return `Elimina una ripetizione di “${operation.word}”.`;
    case "repeat-phrase":
      return `Elimina la prima occorrenza di “${operation.phrase.join(" ")}” quando ripetuta due volte.`;
    case "elision":
      return `Elimina “${operation.first}” prima di “${operation.prefix}…”.`;
  }
}
/** Context limits of the rule, or null when it applies everywhere. */
export function proposalContext(candidate: Candidate) {
  const context = candidate.definition.context;
  const parts = [
    context.before ? `Solo dopo “${context.before}”.` : null,
    context.after ? `Solo prima di “${context.after}”.` : null,
    context.exceptBefore.length
      ? `Esclusa dopo: ${context.exceptBefore.join(", ")}.`
      : null,
    context.exceptAfter.length
      ? `Esclusa prima di: ${context.exceptAfter.join(", ")}.`
      : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" ") : null;
}
export function evidenceSummary(evidence: Proposal["evidence"]) {
  const references = evidence.length;
  const sessions = new Set(evidence.map((e) => e.sessionId)).size;
  const confirmed = evidence.filter((e) => e.humanConfirmed).length;
  return [
    `${references} ${references === 1 ? "riferimento" : "riferimenti"}`,
    `${sessions} ${sessions === 1 ? "sessione" : "sessioni"}`,
    `${confirmed} ${confirmed === 1 ? "correzione umana confermata" : "correzioni umane confermate"}`,
  ].join(" · ");
}
export const reviewFilters = [
  { value: "review", label: "Da revisionare" },
  { value: "deferred", label: "Rimandate" },
  { value: "approved", label: "Approvate e attive" },
  { value: "disabled", label: "Disattivate" },
  { value: "rejected", label: "Rifiutate" },
  { value: "all", label: "Tutte" },
] as const;
export type ReviewFilter = (typeof reviewFilters)[number]["value"];
/** "Da revisionare" keeps the deferred proposals, as the review count does. */
export function matchesFilter(proposal: Proposal, filter: ReviewFilter) {
  if (filter === "all") return true;
  if (filter === "review")
    return proposal.state === "proposed" || proposal.state === "deferred";
  return proposal.state === filter;
}
/** The narrowest filter that shows a proposal reached by a deep link. */
export const filterFor = (proposal: Proposal): ReviewFilter =>
  proposal.state === "proposed" || proposal.state === "deferred"
    ? "review"
    : proposal.state;
