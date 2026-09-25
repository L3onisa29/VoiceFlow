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
});
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

export function formatDuration(seconds: number) {
  const total = Math.round(seconds);
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} min ${total % 60} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}
