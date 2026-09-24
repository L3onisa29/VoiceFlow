import { useCallback, useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import {
  action,
  getPermissions,
  getState,
  openPrivacy,
  saveSettings,
  stateSchema,
  type Action,
  type DictationState,
  type Permissions,
  type Settings,
} from "@/lib/desktop";

export function useDictation() {
  const [state, setState] = useState<DictationState | null>(null);
  const [permissions, setPermissions] = useState<Permissions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const accept = useCallback(
    (next: DictationState) =>
      setState((previous) =>
        !previous || next.revision >= previous.revision ? next : previous,
      ),
    [],
  );
  const refreshPermissions = useCallback(async () => {
    setPermissions(await getPermissions());
  }, []);
  useEffect(() => {
    let disposed = false;
    let release: (() => void) | undefined;
    async function connect() {
      release = await listen("dictation-state", (event) => {
        if (disposed) return;
        const parsed = stateSchema.safeParse(event.payload);
        if (parsed.success) {
          accept(parsed.data);
          setCopied(false);
        } else setError("Aggiornamento dell’app non valido. Riapri VoiceFlow.");
      });
      if (disposed) {
        release();
        return;
      }
      const [snapshot, access] = await Promise.all([
        getState(),
        getPermissions(),
      ]);
      if (!disposed) {
        accept(snapshot);
        setPermissions(access);
      }
    }
    void connect().catch((error) => {
      if (!disposed) setError(String(error));
    });
    const onFocus = () => {
      void refreshPermissions().catch((error) => setError(String(error)));
    };
    window.addEventListener("focus", onFocus);
    const timer = window.setInterval(onFocus, 3000);
    return () => {
      disposed = true;
      release?.();
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [accept, refreshPermissions]);
  const run = async (job: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await job();
    } catch (error) {
      setError(String(error));
    } finally {
      setBusy(false);
    }
  };
  return {
    state,
    permissions,
    error,
    busy,
    copied,
    execute: (name: Action) =>
      run(async () => {
        await action(name);
        if (name === "copy_transcript") setCopied(true);
      }),
    updateSettings: (settings: Settings) => run(() => saveSettings(settings)),
    privacy: (section: "microphone" | "accessibility") =>
      run(() => openPrivacy(section)),
  };
}
