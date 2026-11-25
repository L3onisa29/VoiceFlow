import { useShallow } from "zustand/react/shallow";
import { useDictationStore } from "@/stores/dictation";
import type { DictationState } from "@/lib/desktop";

// Require a selection: consumers cannot accidentally subscribe to all audio events.
export function useDictation<T>(
  selector: (store: ReturnType<typeof useDictationStore.getState>) => T,
): T {
  return useDictationStore(useShallow(selector));
}

export function useDictationState<K extends keyof DictationState>(
  ...keys: K[]
): Pick<DictationState, K> | null {
  return useDictation((store) =>
    store.state === null
      ? null
      : (Object.fromEntries(
          keys.map((key) => [key, store.state![key]]),
        ) as Pick<DictationState, K>),
  );
}
