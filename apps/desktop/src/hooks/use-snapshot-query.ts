import { useEffect, useRef, useState } from "react";
import { invalidateLocalQueries } from "@/lib/query-client";
import {
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import { toUserMessage } from "@/lib/errors";

// Native writes return the authoritative snapshot. Cancel reads before publishing it.
export function useSnapshotQuery<T>(
  key: QueryKey,
  read: () => Promise<T>,
  related: readonly QueryKey[],
  staleTime = 30_000,
) {
  const client = useQueryClient();
  const pending = useRef(false);
  const writing = useIsMutating({ mutationKey: key }) > 0;
  const [error, setError] = useState<string | null>(null);
  // A failed write re-enables the read, which refetches at once. That successful
  // read must not erase the write's error before the person has seen it.
  const writeFailed = useRef(false);
  const query = useQuery({
    queryKey: key,
    queryFn: read,
    enabled: !writing,
    staleTime,
  });
  useEffect(() => {
    if (query.error) {
      writeFailed.current = false;
      setError(toUserMessage(query.error));
    } else if (query.isSuccess && !writeFailed.current) setError(null);
  }, [query.error, query.errorUpdatedAt, query.isSuccess, query.dataUpdatedAt]);
  const mutation = useMutation({
    mutationKey: key,
    mutationFn: (operation: () => Promise<T>) => operation(),
    onMutate: () => client.cancelQueries({ queryKey: key }),
    onSuccess: async (snapshot) => {
      client.setQueryData(key, snapshot);
      await invalidateLocalQueries(client, related);
    },
    onError: (error) => {
      writeFailed.current = true;
      setError(toUserMessage(error));
    },
  });
  const mutate = async (operation: () => Promise<T>) => {
    // A background read never blocks a write: onMutate cancels it, and the write
    // returns the authoritative snapshot anyway.
    if (pending.current || client.isMutating({ mutationKey: key })) return null;
    pending.current = true;
    writeFailed.current = false;
    setError(null);
    try {
      return await mutation.mutateAsync(operation);
    } catch {
      return null;
    } finally {
      pending.current = false;
    }
  };
  const refresh = async () => {
    if (pending.current || writing) return;
    writeFailed.current = false;
    setError(null);
    await query.refetch();
  };
  return {
    data: query.data ?? null,
    // Only the first load locks the page. Refetches on window focus or native
    // events run in the background and must not swallow clicks or keystrokes.
    loading: query.isLoading,
    refreshing: query.isFetching,
    busy: writing,
    error,
    mutate,
    refresh,
    clearError: () => {
      writeFailed.current = false;
      setError(null);
    },
  };
}
