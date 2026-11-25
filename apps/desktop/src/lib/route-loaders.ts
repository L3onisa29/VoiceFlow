import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";
import { dictionaryApi } from "@/lib/dictionary";
import { historyApi } from "@/lib/history";
import { queryKeys } from "@/lib/query-client";

// Route loaders only warm the Query cache, so data starts loading on hover
// (preload) or navigation. Keys and fetchers mirror the page hooks (useHistory,
// useDictionary); those hooks keep owning freshness, errors and writes.

export const historyFirstPageQuery = (search = "") =>
  infiniteQueryOptions({
    queryKey: [...queryKeys.historyPages, search],
    queryFn: ({ pageParam }) => historyApi.page(search, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });

export const dictionaryQuery = queryOptions({
  queryKey: queryKeys.dictionary,
  queryFn: dictionaryApi.read,
});

/**
 * Starts the read without blocking navigation: pages keep their own loading
 * and error states, and a failed warm-up never reaches the route error UI.
 */
export function warm(load: Promise<unknown>): void {
  load.catch(() => undefined);
}
