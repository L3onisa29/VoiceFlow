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
export const historyApi = {
  page: async (search: string, beforeId: number | null = null) =>
    historyPageSchema.parse(
      await invoke("history_page", { query: { search, beforeId } }),
    ),
  copy: (id: number) => invoke<void>("history_copy", { id }),
  delete: (id: number) => invoke<void>("history_delete", { id }),
};

export function formatDuration(seconds: number) {
  const total = Math.round(seconds);
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} min ${total % 60} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}
