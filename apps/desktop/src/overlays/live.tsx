import React, { useCallback, useEffect, useId, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { ChevronDown, ChevronUp } from "lucide-react";
import { CorrectionEditor } from "@/components/corrections/correction-editor";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  buildSteps,
  lessonFor,
  sentenceChanges,
  sentenceStarts,
} from "@/lib/corrections";
import { liveStateSchema, type LiveState } from "@/lib/desktop";
import { toUserMessage } from "@/lib/errors";
import { overlayApi, useFitToContent, type LiveEdit } from "@/lib/overlay";
import { cn } from "@/lib/utils";
import "@/styles/app.css";

const SHOWN = new Set([
  "recording",
  "paused",
  "stopping",
  "finalizing",
  "error",
]);

function useStoredFlag(key: string, initial: boolean) {
  const [value, setValue] = useState(() => {
    try {
      const stored = localStorage.getItem(key);
      return stored === null ? initial : stored === "1";
    } catch {
      return initial;
    }
  });
  const update = useCallback(
    (next: boolean) => {
      setValue(next);
      try {
        localStorage.setItem(key, next ? "1" : "0");
      } catch {
        // Preference only: the panel works without it.
      }
    },
    [key],
  );
  return [value, update] as const;
}

/** `base` is the confirmed text received so far; `draft` is the user's version of it. */
type Doc = { session: number; base: string; draft: string };

function LivePanel() {
  const [state, setState] = useState<LiveState | null>(null);
  const [doc, setDoc] = useState<Doc>({ session: -1, base: "", draft: "" });
  const docRef = useRef(doc);
  docRef.current = doc;
  const [groups, setGroups] = useStoredFlag("vf-live-groups", true);
  const [collapsed, setCollapsed] = useStoredFlag("vf-live-collapsed", false);
  const [active, setActive] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [following, setFollowing] = useState(true);
  const [behind, setBehind] = useState(false);
  const content = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const groupsId = useId();

  const accept = useCallback((next: LiveState) => {
    setState((previous) =>
      previous && next.revision < previous.revision ? previous : next,
    );
    setDoc((current) => {
      const confirmed = next.transcript.confirmed;
      if (next.session !== current.session)
        return { session: next.session, base: confirmed, draft: confirmed };
      if (confirmed === current.base) return current;
      // Confirmed text only grows: new words go after the user's version.
      if (confirmed.startsWith(current.base))
        return {
          ...current,
          base: confirmed,
          draft: current.draft + confirmed.slice(current.base.length),
        };
      return { ...current, base: confirmed, draft: confirmed };
    });
  }, []);

  // ⌃⌥T, registered while dictating, shows or hides the text.
  const collapsedRef = useRef(collapsed);
  collapsedRef.current = collapsed;
  useEffect(() => {
    const unlisten = listen("live-toggle", () =>
      setCollapsed(!collapsedRef.current),
    );
    return () => void unlisten.then((release) => release());
  }, [setCollapsed]);

  useEffect(() => {
    let disposed = false;
    let release: (() => void) | undefined;
    void listen("dictation-state", (event) => {
      const parsed = liveStateSchema.safeParse(event.payload);
      if (!disposed && parsed.success) accept(parsed.data);
    }).then((unlisten) => {
      if (disposed) unlisten();
      else release = unlisten;
    });
    void invoke("get_state").then((raw) => {
      const parsed = liveStateSchema.safeParse(raw);
      if (!disposed && parsed.success) accept(parsed.data);
    });
    return () => {
      disposed = true;
      release?.();
    };
  }, [accept]);

  const phase = state?.phase ?? "idle";
  const shown =
    state !== null &&
    SHOWN.has(phase) &&
    (phase !== "error" || state.recoverable || doc.draft.trim() !== "");
  const editable =
    ["recording", "paused", "stopping"].includes(phase) ||
    (phase === "error" && state?.recoverable === true);

  // The pill appears first; the panel opens above it a moment later.
  useEffect(() => {
    if (!shown) {
      setReady(false);
      setNotice(null);
      void overlayApi.hide("live").catch(() => {});
      return;
    }
    const timer = setTimeout(() => setReady(true), 260);
    return () => clearTimeout(timer);
  }, [shown, doc.session]);

  useFitToContent("live", content, shown && ready);

  // Corrections reach the session in order; only the latest pending one is sent.
  const sending = useRef(false);
  const queued = useRef<LiveEdit | null>(null);
  const flush = useCallback(async () => {
    if (sending.current) return;
    sending.current = true;
    while (queued.current) {
      const edit = queued.current;
      queued.current = null;
      try {
        await overlayApi.liveEdit(edit);
        setNotice(null);
      } catch (error) {
        setNotice(toUserMessage(error));
      }
    }
    sending.current = false;
  }, []);

  const onChange = (next: string) => {
    const { session, base } = docRef.current;
    setDoc((current) => ({ ...current, draft: next }));
    queued.current = {
      session,
      base,
      text: next,
      steps: buildSteps(base, base, next, (_k, before, after) =>
        lessonFor(before, after),
      ).map(({ segment, text, category }) => ({ segment, text, category })),
    };
    void flush();
  };

  // Follow new words unless the user scrolled up or is writing.
  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const writing = element.contains(document.activeElement);
    if (following && !writing) element.scrollTop = element.scrollHeight;
    else if (!writing) setBehind(true);
  }, [doc.draft, state?.transcript.partial, following]);

  const partial = state?.transcript.partial ?? "";
  const sentences = doc.base.trim() ? sentenceStarts(doc.base).length : 0;
  const corrected = sentenceChanges(doc.base, doc.draft).length;
  const release = () => void overlayApi.release().catch(() => {});
  // On error the pill names the failure; the panel says what happens to the text.
  const title =
    phase === "error"
      ? state?.recoverable
        ? "Testo conservato"
        : "Testo salvato nei recenti"
      : phase === "paused"
        ? "In pausa"
        : phase === "stopping" || phase === "finalizing"
          ? "Chiusura…"
          : "Trascrizione live";

  if (!shown) return null;
  return (
    <div
      className="flex h-screen flex-col justify-end"
      onKeyDown={(event) => {
        if (event.key === "Escape") release();
      }}
    >
      <div ref={content} className="flex flex-col items-center pt-1">
        {collapsed ? (
          <Button
            variant="outline"
            size="xs"
            className="mb-1 bg-popover text-popover-foreground shadow-md dark:bg-popover"
            onClick={() => setCollapsed(false)}
          >
            <ChevronUp data-icon="inline-start" />
            Mostra testo
            <Kbd className="bg-transparent">⌃⌥T</Kbd>
          </Button>
        ) : (
          <section
            className="w-full overflow-hidden rounded-lg border bg-popover text-popover-foreground shadow-xl"
            aria-label="Trascrizione live"
          >
            <header className="flex items-center gap-2.5 border-b px-3 py-1.5 text-[11px] text-muted-foreground">
              <span
                className={cn(
                  "size-1.5 shrink-0 rounded-full bg-muted-foreground",
                  phase === "recording" && "animate-pulse bg-primary",
                  phase === "error" && "bg-destructive",
                )}
              />
              <span className="font-medium text-foreground">{title}</span>
              <span className="truncate">
                {sentences > 0 &&
                  `· ${sentences} ${sentences === 1 ? "frase" : "frasi"}`}
                {corrected > 0 &&
                  ` · ${corrected} ${corrected === 1 ? "corretta" : "corrette"}`}
                {active !== null && ` · frase ${active + 1}`}
              </span>
              <span className="flex-1" />
              <div className="flex items-center gap-1.5">
                <Switch
                  id={groupsId}
                  size="sm"
                  checked={groups}
                  onCheckedChange={setGroups}
                />
                <Label htmlFor={groupsId} className="text-[11px] font-normal">
                  Frasi
                </Label>
              </div>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label="Nascondi testo"
                title="Nascondi testo · ⌃⌥T"
                onClick={() => setCollapsed(true)}
              >
                <ChevronDown />
              </Button>
            </header>
            <div
              ref={scroller}
              className="relative max-h-42 overflow-y-auto px-3.5 pt-2.5 pb-3 text-sm leading-7 [scrollbar-width:thin]"
              onScroll={(event) => {
                const element = event.currentTarget;
                const bottom =
                  element.scrollHeight -
                    element.scrollTop -
                    element.clientHeight <
                  12;
                setFollowing(bottom);
                if (bottom) setBehind(false);
              }}
            >
              {doc.draft || partial ? (
                <>
                  <CorrectionEditor
                    label="Testo della dettatura"
                    original={doc.base}
                    value={doc.draft}
                    onChange={onChange}
                    groups={groups}
                    readOnly={!editable}
                    className="inline"
                    onActiveSentence={setActive}
                    onEscape={release}
                  />
                  {partial && (
                    <span className="whitespace-pre-wrap text-muted-foreground italic">
                      {doc.draft && !/\s$/.test(doc.draft) ? " " : ""}
                      {partial}…
                    </span>
                  )}
                </>
              ) : (
                <p className="text-muted-foreground">In ascolto…</p>
              )}
              {behind && (
                <Button
                  size="xs"
                  className="sticky bottom-0 float-right"
                  onClick={() => {
                    const element = scroller.current;
                    if (element) element.scrollTop = element.scrollHeight;
                    setBehind(false);
                    setFollowing(true);
                  }}
                >
                  ↓ nuovo testo
                </Button>
              )}
            </div>
            <footer className="flex flex-wrap items-center justify-between gap-2 border-t px-3 py-1.5 text-[11px] text-muted-foreground">
              <span className={cn(notice && "text-destructive")}>
                {notice ??
                  (phase === "error"
                    ? state?.recoverable
                      ? "Riprova per continuare, chiudi per salvarlo"
                      : "Puoi correggerlo e inserirlo dai recenti"
                    : "Le correzioni finiscono nel testo inserito")}
              </span>
              <span className="flex items-center gap-1.5">
                {phase === "error" && !state?.recoverable ? (
                  <>
                    <Kbd>⌃⌥V</Kbd> recenti
                  </>
                ) : (
                  <>
                    <Kbd>esc</Kbd> torna all'app
                  </>
                )}
              </span>
            </footer>
          </section>
        )}
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <LivePanel />
  </React.StrictMode>,
);
