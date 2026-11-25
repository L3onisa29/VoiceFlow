import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { historyApi } from "@/lib/history";
import { queryKeys } from "@/lib/query-client";
import { useDebouncedValue, useHistoryConnection } from "./use-query-events";
import { toUserMessage } from "@/lib/errors";

export function useHistory(search: string) {
  const client = useQueryClient();
  const debounced = useDebouncedValue(search, 200);
  const waiting = debounced !== search;
  const connectionError = useHistoryConnection((s) => s.error);
  const query = useInfiniteQuery({
    queryKey: [...queryKeys.historyPages, debounced],
    queryFn: ({ pageParam }) => historyApi.page(debounced, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
    enabled: !waiting,
  });
  const pages = query.data?.pages;
  return {
    pages: waiting ? null : (pages ?? null),
    hasNextPage: !waiting && query.hasNextPage,
    // Background refetches (focus, history events) keep the list usable.
    loading: waiting || query.isLoading,
    error: waiting
      ? null
      : query.error
        ? toUserMessage(query.error)
        : connectionError,
    refresh: () => {
      void client.invalidateQueries({ queryKey: queryKeys.historyPages });
    },
    loadMore: async () => {
      if (!waiting && query.hasNextPage && !query.isFetchingNextPage)
        return (await query.fetchNextPage()).isSuccess;
      return false;
    },
  };
}
