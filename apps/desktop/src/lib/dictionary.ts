import { invoke } from "@tauri-apps/api/core";
import { z } from "zod";

const idSchema = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
const termSchema = z.object({
  id: idSchema,
  text: z.string(),
  groupIds: z.array(idSchema),
});
const groupSchema = z.object({ id: idSchema, name: z.string() });
export const dictionarySchema = z.object({
  terms: z.array(termSchema),
  groups: z.array(groupSchema),
});
export type Dictionary = z.infer<typeof dictionarySchema>;
export type Term = z.infer<typeof termSchema>;
export type Group = z.infer<typeof groupSchema>;
export type TermInput = { id: number | null; text: string; groupIds: number[] };
export type GroupInput = { id: number | null; name: string };

async function request(
  command: string,
  args?: Record<string, unknown>,
): Promise<Dictionary> {
  return dictionarySchema.parse(await invoke(command, args));
}
export const dictionaryApi = {
  read: () => request("dictionary_snapshot"),
  saveTerm: (input: TermInput) => request("dictionary_save_term", { input }),
  saveGroup: (input: GroupInput) => request("dictionary_save_group", { input }),
  deleteTerm: (id: number) => request("dictionary_delete_term", { id }),
  deleteGroup: (id: number) => request("dictionary_delete_group", { id }),
};
