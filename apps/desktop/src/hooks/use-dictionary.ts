import { toast } from "sonner";
import { dictionaryApi, type Dictionary } from "@/lib/dictionary";
import { queryKeys } from "@/lib/query-client";
import { toUserMessage } from "@/lib/errors";
import { useSnapshotQuery } from "./use-snapshot-query";

const related = [queryKeys.suggestions, queryKeys.analysis];
export function useDictionary() {
  const query = useSnapshotQuery(
    queryKeys.dictionary,
    dictionaryApi.read,
    related,
  );
  return {
    ...query,
    /** Dialog writes: a failure stays in `error`, shown next to the draft. */
    mutate: async (operation: () => Promise<Dictionary>) =>
      (await query.mutate(operation)) !== null,
    /** Page writes: a failure becomes a toast and never lingers in `error`. */
    write: async (operation: () => Promise<Dictionary>) => {
      let failure: unknown = null;
      const saved = await query.mutate(async () => {
        try {
          return await operation();
        } catch (error) {
          failure = error;
          throw error;
        }
      });
      if (saved !== null) return true;
      query.clearError();
      toast.error(
        failure === null
          ? "Un’altra modifica è in corso. Riprova tra un istante."
          : toUserMessage(failure),
      );
      return false;
    },
  };
}
