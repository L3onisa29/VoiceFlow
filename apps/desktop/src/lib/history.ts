import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";

const count = z.number().int().nonnegative();
const historyEntrySchema = z.object({
  id: z.number().int().positive(),
  createdAt: z.number().int(),
  text: z.string(),
  wordCount: count,
  durationSeconds: z.number().nonnegative(),
  language: z.string(),
  uncertainWords: count.default(0),
  unknownRemoved: count.default(0),
});
const recognitionSchema = z.object({
  meanConfidence: z.number().min(0).max(1).nullable(),
  scoredTokens: count,
  threshold: z.number(),
  lowWords: z.array(z.object({ utterance: count, word: count })),
  lowWordsTotal: count,
  unknownRemoved: count,
  languageTags: z.record(z.string(), count),
});
export type Recognition = z.infer<typeof recognitionSchema>;
const timingSchema = z.object({
  keyToMicrophoneMs: count.nullable(),
  stopToPasteMs: count.nullable(),
  pasted: z.boolean(),
  inferenceMs: count,
  chunks: count,
  silenceClosures: count,
  preparedDecoder: z.boolean(),
});
export type Timing = z.infer<typeof timingSchema>;
const historyPageSchema = z.object({
  entries: z.array(historyEntrySchema),
  nextCursor: z.number().int().positive().nullable(),
  stats: z.object({
    totalWords: count,
    totalSessions: count,
    totalSeconds: z.number().nonnegative(),
    wordsPerMinute: z.number().nonnegative().nullable(),
  }),
});
export type HistoryEntry = z.infer<typeof historyEntrySchema>;
export type HistoryPage = z.infer<typeof historyPageSchema>;
export const correctionCategorySchema = z.enum([
  "recognition",
  "cleanup",
  "style",
]);
export type CorrectionCategory = z.infer<typeof correctionCategorySchema>;
const historyDetailSchema = z.object({
  entry: historyEntrySchema,
  originalAsr: z.array(z.string()).nullable(),
  producedText: z.string(),
  processing: z
    .object({
      modelId: z.string(),
      modelRevision: z.string(),
      modelProvenance: z.string(),
      decoderVersion: z.string(),
      processorVersion: z.string(),
      format: z.enum(["faithful", "spoken-commands"]),
      cleanup: z.boolean(),
      groups: z.array(z.string()),
      dictionaryVersion: z.string().nullable(),
      cleanupVersion: z.string(),
      recognition: recognitionSchema.nullish().default(null),
      ruleHits: z.record(z.string(), count).nullish().default(null),
      timing: timingSchema.nullish().default(null),
    })
    .nullable(),
  editVersion: count,
  currentRevision: z.number().int().positive().nullable(),
  revisions: z.array(
    z.object({
      id: z.number().int().positive(),
      createdAt: z.number().int(),
      beforeText: z.string(),
      text: z.string(),
      reverted: z.boolean(),
      category: correctionCategorySchema.nullable(),
      origin: z.enum(["live", "review"]).nullish().default(null),
      segment: z.number().int().nonnegative().nullish().default(null),
    }),
  ),
});
export type HistoryDetail = z.infer<typeof historyDetailSchema>;
const editRequestSchema = z.object({
  id: z.number().int().positive(),
  expectedVersion: count,
  requestKey: z.string().min(1).max(100),
  text: z
    .string()
    .refine(
      (value) =>
        value.trim().length > 0 &&
        new TextEncoder().encode(value).length <= 1_000_000,
      "Inserisci un testo tra 1 e 1.000.000 byte.",
    ),
});
const recentEntrySchema = z.object({
  id: z.number().int().positive(),
  createdAt: z.number().int(),
  text: z.string(),
  producedText: z.string(),
  durationSeconds: z.number().nonnegative(),
  pasted: z.boolean().nullable(),
  interrupted: z.boolean(),
  destinationApp: z.string().nullable(),
});
export type RecentEntry = z.infer<typeof recentEntrySchema>;
const changeStepSchema = z.object({
  segment: z.number().int().nonnegative(),
  text: z.string().min(1),
  category: correctionCategorySchema.nullable(),
  restore: z.boolean(),
});
const changesRequestSchema = z.object({
  id: z.number().int().positive(),
  expectedVersion: count,
  requestKey: z.string().min(1).max(80),
  origin: z.enum(["live", "review"]),
  steps: z.array(changeStepSchema).min(1).max(500),
});
export const historyApi = {
  page: async (search: string, beforeId: number | null = null) =>
    historyPageSchema.parse(
      await invoke("history_page", { query: { search, beforeId } }),
    ),
  copy: (id: number) => invoke<void>("history_copy", { id }),
  delete: (id: number) => invoke<void>("history_delete", { id }),
  detail: async (id: number) =>
    historyDetailSchema.parse(await invoke("history_detail", { id })),
  edit: async (request: z.infer<typeof editRequestSchema>) =>
    historyDetailSchema.parse(
      await invoke("history_edit", {
        request: editRequestSchema.parse(request),
      }),
    ),
  undo: async (id: number, expectedVersion: number) =>
    historyDetailSchema.parse(
      await invoke("history_undo", { request: { id, expectedVersion } }),
    ),
  recent: async (search: string) =>
    z
      .array(recentEntrySchema)
      .parse(await invoke("history_recent", { query: { search } })),
  saveChanges: async (request: z.infer<typeof changesRequestSchema>) =>
    historyDetailSchema.parse(
      await invoke("history_save_changes", {
        request: changesRequestSchema.parse(request),
      }),
    ),
  forget: async (id: number, revisionId: number) =>
    historyDetailSchema.parse(
      await invoke("history_forget", { request: { id, revisionId } }),
    ),
  remember: async (
    id: number,
    revisionId: number,
    category: CorrectionCategory,
  ) =>
    historyDetailSchema.parse(
      await invoke("history_remember", {
        request: {
          id,
          revisionId,
          category: correctionCategorySchema.parse(category),
        },
      }),
    ),
};

/** Avviso breve per elenco e dettaglio. Vuoto quando non c'è nulla da segnalare. */
export function recognitionWarnings(entry: HistoryEntry) {
  const warnings: string[] = [];
  if (entry.unknownRemoved > 0)
    warnings.push(
      entry.unknownRemoved === 1
        ? "1 parola non riconosciuta e omessa"
        : `${entry.unknownRemoved} parole non riconosciute e omesse`,
    );
  if (entry.uncertainWords > 0)
    warnings.push(
      entry.uncertainWords === 1
        ? "1 parola incerta"
        : `${entry.uncertainWords} parole incerte`,
    );
  return warnings;
}

export function formatDuration(seconds: number) {
  const total = Math.round(seconds);
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} min ${total % 60} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}
