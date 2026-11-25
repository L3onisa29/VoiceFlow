import { useEffect } from "react";
import { listen } from "@tauri-apps/api/event";
import {
  getPermissions,
  getState,
  levelSchema,
  stateSchema,
} from "@/lib/desktop";
import { useDictationStore } from "@/stores/dictation";
import { toUserMessage } from "@/lib/errors";

export function useDictationConnection() {
  useEffect(() => {
    let disposed = false;
    let release: (() => void) | undefined;
    let releaseLevel: (() => void) | undefined;
    let permissionRequest = 0;
    const store = useDictationStore.getState();
    const report = (error: unknown) => {
      if (!disposed) store.setError(toUserMessage(error));
    };
    async function refreshPermissions() {
      const request = ++permissionRequest;
      try {
        const access = await getPermissions();
        if (!disposed && request === permissionRequest)
          store.setPermissions(access);
      } catch (error) {
        if (request === permissionRequest) report(error);
      }
    }
    async function connect() {
      release = await listen("dictation-state", (event) => {
        if (disposed) return;
        const parsed = stateSchema.safeParse(event.payload);
        if (parsed.success) store.accept(parsed.data);
        else
          store.setError(
            "Aggiornamento dell’app non valido. Riapri VoiceFlow.",
          );
      });
      releaseLevel = await listen("dictation-level", (event) => {
        if (disposed) return;
        const parsed = levelSchema.safeParse(event.payload);
        if (parsed.success) store.acceptLevel(parsed.data);
      });
      if (disposed) {
        release();
        releaseLevel();
        return;
      }
      await Promise.all([
        getState().then((snapshot) => {
          if (!disposed) store.accept(snapshot);
        }),
        refreshPermissions(),
      ]);
    }
    void connect().catch(report);
    const onFocus = () => {
      void refreshPermissions();
    };
    window.addEventListener("focus", onFocus);
    const timer = window.setInterval(onFocus, 3000);
    return () => {
      disposed = true;
      release?.();
      releaseLevel?.();
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, []);
}
