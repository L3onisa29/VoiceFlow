import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { invalidateHistory } from "@/lib/query-client";
import { create } from "zustand";
import { toUserMessage } from "@/lib/errors";

export const useHistoryConnection = create<{ error: string | null }>(() => ({
  error: null,
}));

export function useQueryEvents() {
  useEffect(() => {
    let disposed = false;
    let release: (() => void) | undefined;
    void listen("history-changed", () => {
      if (!disposed) void invalidateHistory();
    })
      .then((unlisten) => {
        if (disposed) unlisten();
        else {
          release = unlisten;
          useHistoryConnection.setState({ error: null });
          // Close the gap between reading a snapshot and subscribing.
          void invalidateHistory();
        }
      })
      .catch((error) => {
        if (!disposed)
          useHistoryConnection.setState({ error: toUserMessage(error) });
      });
    return () => {
      disposed = true;
      release?.();
    };
  }, []);
}

export function useDebouncedValue<T>(value: T, delay: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
