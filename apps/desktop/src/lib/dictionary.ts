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
const count = z.number().int().nonnegative();
const autoVariantSchema = z.object({
  spelling: z.string(),
  termId: idSchema,
  term: z.string(),
  addedAt: z.number(),
  seenSessions: count,
  changedSessions: count.nullable(),
  replayedSessions: count.nullable(),
});
export type AutoVariant = z.infer<typeof autoVariantSchema>;
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
  autoVariants: async () =>
    z.array(autoVariantSchema).parse(await invoke("dictionary_auto_variants")),
  /** Toglie la variante e la blocca: non rientra più da sola. */
  removeAutoVariant: async (spelling: string) =>
    z
      .array(autoVariantSchema)
      .parse(await invoke("dictionary_remove_auto_variant", { spelling })),
};

// UI helpers. They only compose the single-item commands above.

export const languageNames: Record<Term["language"], string> = {
  "*": "Tutte le lingue",
  it: "Italiano",
  en: "Inglese",
};
export const languageShortNames: Record<Term["language"], string> = {
  "*": "Tutte",
  it: "IT",
  en: "EN",
};

export type TermFilter = "all" | "ungrouped" | `${number}`;
export type TermSort = "az" | "za";

/** Search matches the preferred spelling and every variant, case-insensitively. */
export function filterTerms(
  terms: readonly Term[],
  search: string,
  filter: TermFilter,
  sort: TermSort,
) {
  const query = search.trim().toLowerCase();
  return terms
    .filter(
      (term) =>
        (term.text.toLowerCase().includes(query) ||
          term.variants.some((variant) =>
            variant.toLowerCase().includes(query),
          )) &&
        (filter === "all" ||
          (filter === "ungrouped"
            ? !term.groupIds.length
            : term.groupIds.includes(Number(filter)))),
    )
    .sort(
      (a, b) =>
        (sort === "za" ? -1 : 1) *
        (a.text.localeCompare(b.text, "it", {
          sensitivity: "base",
          numeric: true,
        }) || a.id - b.id),
    );
}

export function groupTermCounts(terms: readonly Term[]) {
  const counts = new Map<number, number>();
  for (const term of terms)
    for (const id of term.groupIds) counts.set(id, (counts.get(id) ?? 0) + 1);
  return counts;
}

/** Terms that lose their only group, and so become global, when it is deleted. */
export function exclusiveTermCount(terms: readonly Term[], groupId: number) {
  return terms.filter(
    (term) => term.groupIds.length === 1 && term.groupIds[0] === groupId,
  ).length;
}

export type BulkOutcome = {
  snapshot: Dictionary;
  done: number;
  skipped: number;
  failedIds: number[];
  error: unknown;
};

/**
 * Applies one single-term command per id, in order, without a bulk IPC. It starts
 * from a fresh snapshot and every write returns the authoritative one, so each
 * next term is read from the latest revision. `write` returns null to skip a term
 * that needs no change; a term deleted meanwhile is skipped as well. One failed
 * write does not stop the others.
 */
export async function applyToTerms(
  ids: readonly number[],
  write: (term: Term) => Promise<Dictionary> | null,
): Promise<BulkOutcome> {
  let snapshot = await dictionaryApi.read();
  const outcome = { done: 0, skipped: 0, failedIds: [] as number[] };
  let error: unknown = null;
  for (const id of ids) {
    const term = snapshot.terms.find((item) => item.id === id);
    try {
      const pending = term ? write(term) : null;
      if (!pending) {
        outcome.skipped++;
        continue;
      }
      snapshot = await pending;
      outcome.done++;
    } catch (reason) {
      outcome.failedIds.push(id);
      error = reason;
    }
  }
  return { snapshot, error, ...outcome };
}
