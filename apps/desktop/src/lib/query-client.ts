import {
  QueryClient,
  focusManager,
  type QueryKey,
} from "@tanstack/react-query";

export const queryKeys = {
  dictionary: ["dictionary"] as const,
  suggestions: ["suggestions"] as const,
  history: ["history"] as const,
  historyPages: ["history", "pages"] as const,
  analysis: ["analysis"] as const,
  sources: ["analysis", "source"] as const,
};

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // All queries use local Tauri IPC, including when the Mac is offline.
      networkMode: "always",
      retry: false,
      staleTime: 30_000,
      refetchOnWindowFocus: "always",
      refetchOnReconnect: false,
    },
    mutations: { networkMode: "always", retry: false },
  },
});

// Tauri emits focus independently of document visibility. One shared listener.
focusManager.setEventListener((handleFocus) => {
  const onFocus = () => {
    if (!document.hidden) handleFocus();
  };
  const onVisibility = () => handleFocus(!document.hidden);
  window.addEventListener("focus", onFocus);
  document.addEventListener("visibilitychange", onVisibility);
  return () => {
    window.removeEventListener("focus", onFocus);
    document.removeEventListener("visibilitychange", onVisibility);
  };
});

export async function invalidateLocalQueries(
  client: QueryClient,
  keys: readonly QueryKey[],
) {
  // Invalidation alone reuses an initial pending read when no data is cached.
  // Its response predates this event/write, so explicitly discard it first.
  await Promise.all(keys.map((queryKey) => client.cancelQueries({ queryKey })));
  await Promise.all(
    keys.map((queryKey) => client.invalidateQueries({ queryKey })),
  );
}

export function invalidateHistory() {
  return invalidateLocalQueries(queryClient, [
    queryKeys.history,
    queryKeys.analysis,
    queryKeys.suggestions,
  ]);
}

export async function invalidatePrivacy() {
  // Hide previously inspected sources immediately, including pending old reads.
  await queryClient.resetQueries({ queryKey: queryKeys.sources });
  await invalidateHistory();
}
