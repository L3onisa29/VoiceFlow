import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";

const idSchema = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
const termSchema = z.object({
  id: idSchema,
  text: z.string(),
  groupIds: z.array(idSchema),
  variants: z.array(z.string()),
  language: z.enum(["*", "it", "en"]),
  matching: z.enum(["exact", "unicode-lowercase"]),
  context: z.object({
    before: z.string().nullable(),
    after: z.string().nullable(),
    exceptBefore: z.array(z.string()),
    exceptAfter: z.array(z.string()),
  }),
});
const groupSchema = z.object({ id: idSchema, name: z.string() });
export const dictionarySchema = z.object({
  terms: z.array(termSchema),
  groups: z.array(groupSchema),
  selectedGroupIds: z.array(idSchema),
  version: z.number().int().positive(),
  conflicts: z.array(idSchema),
});
export type Dictionary = z.infer<typeof dictionarySchema>;
export type Term = z.infer<typeof termSchema>;
export type Group = z.infer<typeof groupSchema>;
export type TermInput = Omit<Term, "id"> & { id: number | null };
export const emptyTermRule = {
  variants: [],
  language: "*",
  matching: "exact",
  context: { before: null, after: null, exceptBefore: [], exceptAfter: [] },
} satisfies Omit<TermInput, "id" | "text" | "groupIds">;
export type GroupInput = { id: number | null; name: string };

async function request(
  command: string,
  args?: Record<string, unknown>,
): Promise<Dictionary> {
  return dictionarySchema.parse(await invoke(command, args));
}
export const dictionaryApi = {
  read: () => request("dictionary_snapshot"),
  selectGroups: (ids: number[]) => request("dictionary_select_groups", { ids }),
  saveTerm: (input: TermInput) => request("dictionary_save_term", { input }),
  saveGroup: (input: GroupInput) => request("dictionary_save_group", { input }),
  deleteTerm: (id: number) => request("dictionary_delete_term", { id }),
  deleteGroup: (id: number) => request("dictionary_delete_group", { id }),
};
