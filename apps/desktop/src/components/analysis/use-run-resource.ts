import { useQuery, type QueryKey } from "@tanstack/react-query";
import { toUserMessage } from "@/lib/errors";

// Keys include route/filter identity; late native responses stay in their own query.
export function useRunResource<T>(
  queryKey: QueryKey,
  load: () => Promise<T>,
  interval: (data: T) => number | null,
  sensitive = false,
) {
  const query = useQuery({
    queryKey,
    queryFn: load,
    staleTime: 0,
    // Source text is rechecked on every opening, never reused across dialogs.
    ...(sensitive ? { gcTime: 0 } : {}),
    refetchInterval: (query) =>
      query.state.status === "error" || query.state.data === undefined
        ? false
        : (interval(query.state.data) ?? false),
    refetchIntervalInBackground: false,
  });
  return {
    data:
      query.isError || (sensitive && !query.isFetchedAfterMount)
        ? null
        : (query.data ?? null),
    error: query.error ? toUserMessage(query.error) : null,
    busy: query.isFetching,
    refresh: () => {
      void query.refetch();
    },
  };
}
