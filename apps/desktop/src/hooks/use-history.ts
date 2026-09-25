import { useCallback, useEffect, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { historyApi, type HistoryPage } from "@/lib/history";

export function useHistory(search: string) {
  const [data, setData] = useState<HistoryPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const load = useCallback(
    async (beforeId: number | null = null) => {
      const current = ++request.current;
      setLoading(true);
      setError(null);
      try {
        const page = await historyApi.page(search, beforeId);
        if (current === request.current) {
          setData((previous) =>
            beforeId && previous
              ? { ...page, entries: [...previous.entries, ...page.entries] }
              : page,
          );
        }
      } catch (error) {
        if (current === request.current) setError(String(error));
      } finally {
        if (current === request.current) setLoading(false);
      }
    },
    [search],
  );

  useEffect(() => {
    // Invalidate immediately so old searches cannot replace newer results.
    ++request.current;
    setLoading(true);
    const timer = window.setTimeout(() => void load(), 200);
    return () => {
      window.clearTimeout(timer);
      ++request.current;
    };
  }, [load, revision]);

  useEffect(() => {
    let disposed = false;
    let release: (() => void) | undefined;
    void listen("history-changed", refresh)
      .then((unlisten) => {
        if (disposed) unlisten();
        else {
          release = unlisten;
          refresh();
        }
      })
      .catch((error) => {
        if (!disposed) setError(String(error));
      });
    window.addEventListener("focus", refresh);
    return () => {
      disposed = true;
      release?.();
      window.removeEventListener("focus", refresh);
    };
  }, [refresh]);

  return {
    data,
    loading,
    error,
    refresh,
    loadMore: () =>
      data?.nextCursor && !loading ? load(data.nextCursor) : undefined,
  };
}
