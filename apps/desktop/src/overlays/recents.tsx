import React, {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactDOM from "react-dom/client";
import { emit, listen } from "@tauri-apps/api/event";
import { ChevronUp, Search, Undo2 } from "lucide-react";
import {
  ADDED,
  CorrectionEditor,
  REMOVED,
} from "@/components/corrections/correction-editor";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Kbd } from "@/components/ui/kbd";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  buildSteps,
  lessonFor,
  originalSentences,
  sentenceChanges,
  sentenceTexts,
  type DiffPart,
  type Lesson,
} from "@/lib/corrections";
import { toUserMessage } from "@/lib/errors";
import {
  formatDuration,
  historyApi,
  type HistoryDetail,
  type RecentEntry,
} from "@/lib/history";
import { overlayApi, useFitToContent } from "@/lib/overlay";
import { cn } from "@/lib/utils";
import "@/styles/app.css";

const LESSONS: (Lesson | null)[] = ["recognition", "cleanup", "style", null];
const LESSON_LABEL: Record<Lesson | "content", string> = {
  recognition: "Riconoscimento",
  cleanup: "Pulizia",
  style: "Stile",
  content: "Contenuto",
};
const lessonLabel = (lesson: Lesson | null) =>
  LESSON_LABEL[lesson ?? "content"];
const SCROLL = "overflow-y-auto [scrollbar-width:thin]";

const relative = new Intl.RelativeTimeFormat("it", { numeric: "auto" });
function ago(createdAt: number) {
  const minutes = Math.round((Date.now() - createdAt) / 60_000);
  if (minutes < 1) return "ora";
  if (minutes < 60) return relative.format(-minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (hours < 24) return relative.format(-hours, "hour");
  return new Date(createdAt).toLocaleString("it-IT", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
function day(createdAt: number) {
  const date = new Date(createdAt);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return "Oggi";
  if (date.toDateString() === yesterday.toDateString()) return "Ieri";
  return date.toLocaleDateString("it-IT", { day: "numeric", month: "long" });
}
function status(entry: RecentEntry) {
  if (entry.interrupted) return "Interrotta";
  if (entry.pasted === true) return "Inserito";
  if (entry.pasted === false) return "Solo copiato";
  return null;
}

/** One correction in a line: the changed part with a little context around it. */
function Snippet({ parts }: { parts: DiffPart[] }) {
  const first = parts.findIndex(([kind]) => kind !== "=");
  const last =
    parts.length - 1 - [...parts].reverse().findIndex(([kind]) => kind !== "=");
  return (
    <>
      {parts.map(([kind, value], k) => {
        if (kind === "=") {
          const text =
            k < first && value.length > 26
              ? `…${value.slice(-26)}`
              : k > last && value.length > 22
                ? `${value.slice(0, 22)}…`
                : value;
          return <span key={k}>{text}</span>;
        }
        if (!value.trim())
          return kind === "-" ? (
            <mark
              key={k}
              className={cn(REMOVED, "px-0.5 font-bold no-underline")}
            >
              ·
            </mark>
          ) : (
            <span key={k}>{value}</span>
          );
        return (
          <mark key={k} className={kind === "-" ? REMOVED : ADDED}>
            {value}
          </mark>
        );
      })}
    </>
  );
}

function ShortcutButton({
  keys,
  children,
  ...props
}: React.ComponentProps<typeof Button> & { keys: string }) {
  return (
    <Button {...props}>
      {children}
      <Kbd className="bg-transparent text-current opacity-70">{keys}</Kbd>
    </Button>
  );
}

function List({
  entries,
  selected,
  onSelect,
  onInsert,
}: {
  entries: RecentEntry[];
  selected: number;
  onSelect: (index: number) => void;
  onInsert: (entry: RecentEntry) => void;
}) {
  const list = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    list.current
      ?.querySelector<HTMLElement>("[data-selected='true']")
      ?.scrollIntoView({ block: "nearest" });
  }, [selected, entries]);
  if (!entries.length)
    return (
      <p className="px-4 py-6 text-center text-xs text-muted-foreground">
        Nessuna trascrizione trovata
      </p>
    );
  let group = "";
  return (
    <div
      ref={list}
      role="listbox"
      aria-label="Trascrizioni recenti"
      className={cn("max-h-75 p-1.5", SCROLL)}
    >
      {entries.map((entry, index) => {
        const heading = day(entry.createdAt);
        const header = heading !== group ? (group = heading) : null;
        const corrections = sentenceChanges(
          entry.producedText,
          entry.text,
        ).length;
        const label = status(entry);
        const isSelected = index === selected;
        return (
          <React.Fragment key={entry.id}>
            {header && (
              <div className="px-2.5 pt-2 pb-1 text-[10.5px] tracking-wider text-muted-foreground uppercase">
                {header}
              </div>
            )}
            <div
              role="option"
              aria-selected={isSelected}
              data-selected={isSelected}
              onClick={() => onSelect(index)}
              onDoubleClick={() => onInsert(entry)}
              className={cn(
                "grid cursor-pointer grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 rounded-lg px-2.5 py-2",
                isSelected && "bg-primary/10 ring-1 ring-primary/35 ring-inset",
              )}
            >
              <div
                className={cn(
                  "overflow-hidden text-[13.5px] leading-snug",
                  isSelected
                    ? "line-clamp-3"
                    : "line-clamp-1 text-foreground/85",
                )}
              >
                {entry.text}
              </div>
              <div className="pt-0.5 text-[11.5px] whitespace-nowrap text-muted-foreground">
                {ago(entry.createdAt)}
              </div>
              <div className="col-span-2 text-[11.5px] text-muted-foreground">
                {[
                  entry.destinationApp,
                  formatDuration(entry.durationSeconds),
                  label,
                ]
                  .filter(Boolean)
                  .map((item, k) => (
                    <React.Fragment key={k}>
                      {k > 0 && " · "}
                      <span
                        className={cn(
                          item === "Interrotta" && "text-destructive",
                        )}
                      >
                        {item}
                      </span>
                    </React.Fragment>
                  ))}
                {corrections > 0 && (
                  <span className="text-primary">
                    {" · "}
                    {corrections}{" "}
                    {corrections === 1 ? "correzione" : "correzioni"}
                  </span>
                )}
              </div>
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
}

type Row = {
  segment: number;
  parts: DiffPart[];
  before: string;
  after: string;
  saved: boolean;
  revision: HistoryDetail["revisions"][number] | undefined;
  lesson: Lesson | null;
};

function Editor({
  entry,
  onBack,
  onDone,
}: {
  entry: RecentEntry;
  onBack: () => void;
  onDone: () => void;
}) {
  const [detail, setDetail] = useState<HistoryDetail | null>(null);
  const [draft, setDraft] = useState(entry.text);
  const [overrides, setOverrides] = useState<Map<number, Lesson | null>>(
    () => new Map(),
  );
  const [compare, setCompare] = useState(false);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rowsRef = useRef<HTMLDivElement>(null);
  const clipRef = useRef<HTMLDivElement>(null);
  const compareId = useId();

  const load = useCallback(async () => {
    try {
      const next = await historyApi.detail(entry.id);
      setDetail(next);
      setDraft(next.entry.text);
      setOverrides(new Map());
    } catch (error) {
      setError(toUserMessage(error));
    }
  }, [entry.id]);
  useEffect(() => {
    void load();
  }, [load]);

  const produced = detail?.producedText ?? entry.producedText;
  const savedText = detail?.entry.text ?? entry.text;
  const rows: Row[] = useMemo(() => {
    const savedSentences = sentenceTexts(produced, savedText);
    return sentenceChanges(produced, draft).map(
      ({ segment, parts, before, after }) => {
        const saved = savedSentences[segment] === after;
        const revision = detail?.revisions.find(
          (item) => item.segment === segment && !item.reverted,
        );
        const lesson = overrides.has(segment)
          ? (overrides.get(segment) ?? null)
          : saved
            ? (revision?.category ?? null)
            : lessonFor(before, after);
        return { segment, parts, before, after, saved, revision, lesson };
      },
    );
  }, [produced, savedText, draft, detail, overrides]);
  const unsaved = rows.filter((row) => !row.saved).length;
  const dirty =
    draft !== savedText ||
    rows.some(
      (row) =>
        row.saved &&
        overrides.has(row.segment) &&
        row.revision?.category !== row.lesson,
    );
  const restored = useMemo(() => {
    const original = originalSentences(produced);
    const saved = sentenceTexts(produced, savedText);
    const wanted = sentenceTexts(produced, draft);
    return wanted.filter((text, k) => text === original[k] && saved[k] !== text)
      .length;
  }, [produced, savedText, draft]);

  // At most three and a half corrections are visible; the rest scrolls. The panel is
  // anchored at its bottom edge, so opening the list grows it upward.
  useLayoutEffect(() => {
    const list = rowsRef.current;
    const clip = clipRef.current;
    if (!list || !clip) return;
    const items = [...list.children] as HTMLElement[];
    list.style.maxHeight =
      items.length > 3
        ? `${items.slice(0, 3).reduce((sum, item) => sum + item.offsetHeight, 0) + items[3].offsetHeight / 2}px`
        : "";
    clip.style.height = open ? `${list.offsetHeight}px` : "0px";
  }, [open, rows]);

  async function save(insert: boolean) {
    if (!detail || busy) return;
    setBusy(true);
    setError(null);
    try {
      let result = detail;
      const steps = buildSteps(
        produced,
        savedText,
        draft,
        (segment, before, after) =>
          overrides.has(segment)
            ? (overrides.get(segment) ?? null)
            : lessonFor(before, after),
      );
      const changedNow = new Set(steps.map((step) => step.segment));
      if (steps.length)
        result = await historyApi.saveChanges({
          id: entry.id,
          expectedVersion: detail.editVersion,
          requestKey: crypto.randomUUID(),
          origin: "review",
          steps,
        });
      // A saved sentence whose category changed: re-label its latest revision.
      for (const row of rows) {
        if (
          !row.saved ||
          changedNow.has(row.segment) ||
          !overrides.has(row.segment)
        )
          continue;
        const revision = result.revisions.find(
          (item) => item.segment === row.segment && !item.reverted,
        );
        if (!revision || revision.category === row.lesson) continue;
        result = row.lesson
          ? await historyApi.remember(entry.id, revision.id, row.lesson)
          : await historyApi.forget(entry.id, revision.id);
      }
      setDetail(result);
      setDraft(result.entry.text);
      setOverrides(new Map());
      void emit("history-changed");
      if (insert) {
        await overlayApi.insert(result.entry.text);
        onDone();
      }
    } catch (error) {
      setError(toUserMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const restore = (segment: number) => {
    const sentences = sentenceTexts(produced, draft);
    sentences[segment] = originalSentences(produced)[segment];
    setDraft(sentences.join(""));
    setOverrides((current) => {
      const next = new Map(current);
      next.delete(segment);
      return next;
    });
  };
  const cycle = (row: Row) => {
    const next = LESSONS[(LESSONS.indexOf(row.lesson) + 1) % LESSONS.length];
    setOverrides((current) => new Map(current).set(row.segment, next));
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const command = event.metaKey || event.ctrlKey;
      if (event.key === "Escape") {
        event.preventDefault();
        onBack();
      } else if (command && event.key === "Enter") {
        event.preventDefault();
        void save(true);
      } else if (command && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void save(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const meta = [
    new Date(entry.createdAt).toLocaleString("it-IT", {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    }),
    formatDuration(entry.durationSeconds),
    status(entry),
  ].filter(Boolean);

  return (
    <>
      <div className="border-b px-4 py-2.5">
        <p className="text-[13px] font-semibold">
          {entry.interrupted
            ? "Dettatura interrotta"
            : entry.destinationApp
              ? `Dettatura in ${entry.destinationApp}`
              : "Dettatura"}
        </p>
        <p className="text-[11.5px] text-muted-foreground">
          {meta.join(" · ")}
        </p>
      </div>
      <div
        className={cn(
          "max-h-50 px-4 pt-3.5 pb-1.5 text-[14.5px] leading-7",
          SCROLL,
        )}
      >
        <CorrectionEditor
          label="Testo da correggere"
          original={produced}
          value={draft}
          onChange={setDraft}
          compare={compare}
          groups={false}
          readOnly={!detail || busy}
        />
      </div>
      {error && <p className="px-4 pb-2 text-xs text-destructive">{error}</p>}
      <div className="border-t px-1.5 py-1">
        <div
          ref={clipRef}
          className="h-0 overflow-hidden transition-[height] duration-300 ease-out"
        >
          <div ref={rowsRef} className={SCROLL}>
            {rows.length === 0 ? (
              <p className="px-2.5 py-2 text-xs text-muted-foreground">
                Nessuna correzione: il testo coincide con quello prodotto.
              </p>
            ) : (
              rows.map((row) => (
                <div
                  key={row.segment}
                  className="grid grid-cols-[1fr_auto_auto] items-center gap-2 rounded-lg px-2.5 py-1.5 text-[13px] hover:bg-accent/50"
                >
                  <div className="min-w-0">
                    <div className="truncate">
                      <Snippet parts={row.parts} />
                    </div>
                    <div
                      className={cn(
                        "mt-0.5 text-[11px] text-muted-foreground",
                        !row.saved && "text-primary",
                      )}
                    >
                      frase {row.segment + 1} ·{" "}
                      {!row.saved
                        ? "non ancora salvata"
                        : row.revision?.origin === "live"
                          ? "salvata · durante la registrazione"
                          : row.revision?.origin === "review"
                            ? "salvata · in revisione"
                            : "salvata"}
                    </div>
                  </div>
                  <Button
                    variant="outline"
                    size="xs"
                    disabled={row.saved && !row.revision}
                    title="Cambia categoria"
                    onClick={() => cycle(row)}
                    className={cn(
                      row.lesson === "recognition" &&
                        "border-primary/50 text-primary",
                    )}
                  >
                    {lessonLabel(row.lesson)}
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`Ripristina la frase ${row.segment + 1}`}
                    title="Ripristina il testo prodotto"
                    onClick={() => restore(row.segment)}
                  >
                    <Undo2 />
                  </Button>
                </div>
              ))
            )}
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          className="w-full justify-start text-[11px] tracking-wider text-muted-foreground uppercase"
        >
          <ChevronUp
            className={cn("transition-transform", open && "rotate-180")}
          />
          Modifiche ({rows.length})
          {unsaved + restored > 0 && (
            <span className="tracking-normal text-primary normal-case">
              · {unsaved + restored} da salvare
            </span>
          )}
          <span className="ml-auto font-normal tracking-normal normal-case">
            riconoscimento → dizionario
          </span>
        </Button>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t px-3 py-2.5">
        <div className="flex items-center gap-3">
          <ShortcutButton
            variant="outline"
            size="sm"
            keys="esc"
            onClick={onBack}
          >
            {dirty ? "Annulla" : "Indietro"}
          </ShortcutButton>
          <div className="flex items-center gap-1.5">
            <Switch
              id={compareId}
              size="sm"
              checked={compare}
              onCheckedChange={setCompare}
            />
            <Label
              htmlFor={compareId}
              className="text-xs font-normal text-muted-foreground"
            >
              Confronta con originale
            </Label>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <ShortcutButton
            variant="outline"
            size="sm"
            keys="⌘S"
            disabled={!dirty || busy}
            onClick={() => void save(false)}
          >
            Salva
          </ShortcutButton>
          <ShortcutButton
            size="sm"
            keys="⌘↵"
            disabled={busy || !draft.trim()}
            onClick={() => void save(true)}
          >
            {dirty ? "Salva e inserisci" : "Inserisci"}
          </ShortcutButton>
        </div>
      </div>
    </>
  );
}

function Recents() {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<RecentEntry[]>([]);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(0);
  const [editing, setEditing] = useState<RecentEntry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const content = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const request = useRef(0);

  const load = useCallback(async (query: string) => {
    const id = ++request.current;
    try {
      const next = await historyApi.recent(query);
      if (id !== request.current) return;
      setEntries(next);
      setSelected(0);
      setError(null);
    } catch (error) {
      if (id === request.current) setError(toUserMessage(error));
    }
  }, []);

  useEffect(() => {
    const releases = [
      listen("recents-open", () => {
        setSearch("");
        setEditing(null);
        setOpen(true);
        void load("");
      }),
      listen("overlay-hidden", () => setOpen(false)),
    ];
    return () => {
      for (const release of releases)
        void release.then((unlisten) => unlisten());
    };
  }, [load]);

  useFitToContent("recents", content, open);
  // The search field takes the keyboard whenever the list is shown.
  useEffect(() => {
    if (open && !editing) input.current?.focus();
  }, [open, editing]);

  const hide = () => {
    setOpen(false);
    void overlayApi.hide("recents").catch(() => {});
  };
  const insert = async (text: string) => {
    setOpen(false);
    try {
      await overlayApi.insert(text);
    } catch (error) {
      setError(toUserMessage(error));
    }
  };
  const copy = (entry: RecentEntry) =>
    void historyApi
      .copy(entry.id)
      .then(hide, (error) => setError(toUserMessage(error)));
  const current = entries[selected];

  useEffect(() => {
    if (editing) return;
    const onKey = (event: KeyboardEvent) => {
      const command = event.metaKey || event.ctrlKey;
      const field = input.current;
      const hasSelection = field && field.selectionStart !== field.selectionEnd;
      if (event.key === "Escape") {
        event.preventDefault();
        hide();
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        setSelected((index) => Math.min(entries.length - 1, index + 1));
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setSelected((index) => Math.max(0, index - 1));
      } else if (event.key === "Enter" && !command && current) {
        event.preventDefault();
        void insert(current.text);
      } else if (command && event.key.toLowerCase() === "e" && current) {
        event.preventDefault();
        setEditing(current);
      } else if (
        command &&
        event.key.toLowerCase() === "c" &&
        current &&
        !hasSelection
      ) {
        event.preventDefault();
        copy(current);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!open) return null;
  return (
    <div className="flex h-screen flex-col justify-end">
      <div ref={content}>
        <section
          aria-label="Trascrizioni recenti"
          className="overflow-hidden rounded-lg border bg-popover text-popover-foreground shadow-2xl"
        >
          {editing ? (
            <Editor
              key={editing.id}
              entry={editing}
              onBack={() => {
                setEditing(null);
                void load(search);
              }}
              onDone={() => setOpen(false)}
            />
          ) : (
            <>
              <label className="flex items-center gap-1 border-b px-2.5 py-1">
                <Search className="size-3.5 shrink-0 text-muted-foreground" />
                <Input
                  ref={input}
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    void load(event.target.value);
                  }}
                  placeholder="Cerca nelle trascrizioni…"
                  aria-label="Cerca nelle trascrizioni"
                  autoComplete="off"
                  spellCheck={false}
                  className="border-0 bg-transparent shadow-none focus-visible:ring-0 dark:bg-transparent"
                />
              </label>
              {error && (
                <p className="px-4 pt-2 text-xs text-destructive">{error}</p>
              )}
              <List
                entries={entries}
                selected={selected}
                onSelect={setSelected}
                onInsert={(entry) => void insert(entry.text)}
              />
              <div className="flex flex-wrap items-center justify-between gap-2 border-t px-2 py-1.5">
                <div className="flex flex-wrap items-center">
                  <ShortcutButton
                    variant="ghost"
                    size="xs"
                    keys="↵"
                    disabled={!current}
                    onClick={() => current && void insert(current.text)}
                  >
                    Inserisci
                  </ShortcutButton>
                  <ShortcutButton
                    variant="ghost"
                    size="xs"
                    keys="⌘C"
                    disabled={!current}
                    onClick={() => current && copy(current)}
                  >
                    Copia
                  </ShortcutButton>
                  <ShortcutButton
                    variant="ghost"
                    size="xs"
                    keys="⌘E"
                    disabled={!current}
                    onClick={() => current && setEditing(current)}
                  >
                    Correggi
                  </ShortcutButton>
                </div>
                <ShortcutButton
                  variant="ghost"
                  size="xs"
                  keys="esc"
                  onClick={hide}
                >
                  Chiudi
                </ShortcutButton>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <Recents />
  </React.StrictMode>,
);
