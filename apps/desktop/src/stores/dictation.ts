import { create } from "zustand";
import { shallow } from "zustand/shallow";
import {
  action,
  openPrivacy,
  saveSettings,
  type Action,
  type DictationState,
  type LevelUpdate,
  type Permissions,
  type Settings,
} from "@/lib/desktop";
import { toUserMessage } from "@/lib/errors";

type DictationStore = {
  state: DictationState | null;
  permissions: Permissions | null;
  error: string | null;
  busy: boolean;
  copied: boolean;
  accept: (next: DictationState) => void;
  acceptLevel: (next: LevelUpdate) => void;
  setPermissions: (next: Permissions) => void;
  setError: (error: string) => void;
  execute: (name: Action) => Promise<void>;
  updateSettings: (settings: Settings) => Promise<void>;
  privacy: (section: "microphone" | "accessibility") => Promise<void>;
};

// Rust owns the session. This store is an in-memory projection for the UI.
export const useDictationStore = create<DictationStore>((set, get) => {
  let pending = 0;
  const run = async (job: () => Promise<void>) => {
    pending++;
    set({ busy: true, error: null });
    try {
      await job();
    } catch (error) {
      set({ error: toUserMessage(error) });
    } finally {
      pending--;
      set({ busy: pending > 0 });
    }
  };
  return {
    state: null,
    permissions: null,
    error: null,
    busy: false,
    copied: false,
    accept: (next) =>
      set((current) => {
        const previous = current.state;
        if (previous && next.revision < previous.revision) return current;
        return {
          state: {
            ...next,
            settings:
              previous && shallow(previous.settings, next.settings)
                ? previous.settings
                : next.settings,
            transcript:
              previous && shallow(previous.transcript, next.transcript)
                ? previous.transcript
                : next.transcript,
          },
          copied: false,
        };
      }),
    // Only for the state it was measured in: a late level never follows a transition.
    acceptLevel: ({ revision, level }) =>
      set((current) =>
        current.state &&
        current.state.revision === revision &&
        current.state.level !== level
          ? { state: { ...current.state, level } }
          : current,
      ),
    setPermissions: (permissions) =>
      set((current) =>
        shallow(current.permissions, permissions) ? current : { permissions },
      ),
    setError: (error) => set({ error }),
    execute: (name) =>
      run(async () => {
        const revision = get().state?.revision;
        await action(name);
        if (name === "copy_transcript" && get().state?.revision === revision)
          set({ copied: true });
      }),
    updateSettings: (settings) => run(() => saveSettings(settings)),
    privacy: (section) => run(() => openPrivacy(section)),
  };
});

export const selectProcessing = ({ state }: DictationStore) =>
  state?.phase === "loading" ||
  state?.phase === "stopping" ||
  state?.phase === "finalizing";
export const selectLocked = (store: DictationStore) =>
  store.busy ||
  selectProcessing(store) ||
  store.state?.phase === "recording" ||
  store.state?.phase === "paused";
