import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  Bookmark,
  ChevronDown,
  Copy,
  History,
  RotateCcw,
  Save,
  Undo2,
} from "lucide-react";
import { toast } from "sonner";
import { invalidateHistory } from "@/lib/query-client";
import { SessionPrivacy } from "@/components/analysis/session-privacy";
import { PageHeader } from "@/components/layout/page";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ButtonGroup } from "@/components/ui/button-group";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Item, ItemContent, ItemHeader } from "@/components/ui/item";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useShortcut } from "@/hooks/use-shortcut";
import { diffParts } from "@/lib/corrections";
import {
  correctionCategorySchema,
  formatDuration,
  historyApi,
  recognitionWarnings,
  type CorrectionCategory,
  type HistoryDetail,
  type Recognition,
  type Timing,
} from "@/lib/history";
import { toUserMessage } from "@/lib/errors";
import { formatDateTime, formatWords, isoDate } from "./format";

const categories: Record<CorrectionCategory, string> = {
  recognition: "Riconoscimento",
  cleanup: "Pulizia",
  style: "Stile",
};

type Commit = (result: HistoryDetail) => void;

export function HistoryEditor({
  id,
  onBusy,
  onDirty,
}: {
  id: number;
  onBusy: (busy: boolean) => void;
  onDirty: (dirty: boolean) => void;
}) {
  const client = useQueryClient();
  const detailKey = ["history", "detail", id] as const;
  const detailQuery = useQuery({
    queryKey: detailKey,
    queryFn: () => historyApi.detail(id),
    gcTime: 0,
    staleTime: 0,
    refetchOnWindowFocus: false,
  });
  // Keep the edit base/version fixed until the draft is saved or discarded.
  const [detail, setDetail] = useState<HistoryDetail | null>(null);
  const initialized = useRef<number | null>(null);
  const [draft, setDraft] = useState("");
  const [category, setCategory] = useState<CorrectionCategory | null>(null);
  const [busy, setBusy] = useState(false);
  const attempt = useRef<{ text: string; version: number; key: string } | null>(
    null,
  );
  useEffect(() => {
    if (detailQuery.data && initialized.current !== id) {
      initialized.current = id;
      setDetail(detailQuery.data);
      setDraft(detailQuery.data.entry.text);
    }
  }, [detailQuery.data, id]);

  const dirty = detail !== null && draft !== detail.entry.text;
  const canSave = dirty && draft.trim().length > 0 && !busy;
  const current = detail?.revisions.find(
    (revision) => revision.id === detail.currentRevision,
  );

  async function mutate(
    operation: (commit: Commit) => Promise<HistoryDetail>,
    message: string,
  ) {
    setBusy(true);
    onBusy(true);
    const commit: Commit = (result) => {
      client.setQueryData(detailKey, result);
      setDetail(result);
      setDraft(result.entry.text);
      onDirty(false);
      setCategory(null);
      void invalidateHistory();
    };
    try {
      await client.cancelQueries({ queryKey: detailKey });
      commit(await operation(commit));
      toast.success(message);
    } catch (error) {
      toast.error(toUserMessage(error));
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  /** The same draft and version retry with the same key: the save is idempotent. */
  function editRequest(base: HistoryDetail) {
    if (
      attempt.current?.text !== draft ||
      attempt.current.version !== base.editVersion
    ) {
      attempt.current = {
        text: draft,
        version: base.editVersion,
        key: crypto.randomUUID(),
      };
    }
    return {
      id,
      expectedVersion: base.editVersion,
      requestKey: attempt.current.key,
      text: draft,
    };
  }
  async function save() {
    if (!detail || !canSave) return;
    const request = editRequest(detail);
    await mutate(() => historyApi.edit(request), "Correzione salvata.");
  }
  async function saveAndRemember(lesson: CorrectionCategory) {
    if (!detail || !canSave) return;
    const request = editRequest(detail);
    await mutate(async (commit) => {
      const saved = await historyApi.edit(request);
      if (saved.currentRevision === null) return saved;
      // The correction is saved even if remembering it fails.
      commit(saved);
      return historyApi.remember(id, saved.currentRevision, lesson);
    }, "Correzione salvata e ricordata.");
  }
  function discard() {
    if (!detail) return;
    setDraft(detail.entry.text);
    onDirty(false);
  }
  async function copy() {
    setBusy(true);
    onBusy(true);
    try {
      await historyApi.copy(id);
      toast.success("Testo copiato.");
    } catch (error) {
      toast.error(toUserMessage(error));
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  useShortcut("mod+s", () => void save(), {
    enabled: canSave,
    allowInInputs: true,
  });

  const backLink = (
    <Button asChild variant="ghost" size="sm" className="self-start">
      <Link to="/history" aria-label="Torna allo storico">
        <ArrowLeft data-icon="inline-start" />
        Storico
      </Link>
    </Button>
  );

  if (!detail) {
    return (
      <>
        {backLink}
        <PageHeader
          title={<span id="transcription-title">Trascrizione</span>}
          description={
            detailQuery.error ? undefined : <Skeleton className="h-4 w-56" />
          }
        />
        {detailQuery.error ? (
          <Alert variant="destructive">
            <AlertTitle>Trascrizione non disponibile</AlertTitle>
            <AlertDescription className="flex flex-col items-start gap-2">
              <span>{toUserMessage(detailQuery.error)}</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void detailQuery.refetch()}
              >
                <RotateCcw data-icon="inline-start" />
                Riprova
              </Button>
            </AlertDescription>
          </Alert>
        ) : (
          <div className="flex flex-col gap-3" aria-hidden="true">
            <Skeleton className="h-4 w-28" />
            <Skeleton className="h-40 w-full" />
            <Skeleton className="h-8 w-64" />
          </div>
        )}
      </>
    );
  }

  const warnings = recognitionWarnings(detail.entry);
  return (
    <>
      {backLink}
      <PageHeader
        title={<span id="transcription-title">Trascrizione</span>}
        description={
          <>
            <time dateTime={isoDate(detail.entry.createdAt)}>
              {formatDateTime(detail.entry.createdAt)}
            </time>
            {" · "}
            {formatDuration(detail.entry.durationSeconds)}
            {" · "}
            {formatWords(detail.entry.wordCount)}
            {" · "}
            {detail.entry.language}
          </>
        }
      />
      {warnings.length > 0 && (
        <Alert aria-label="Avvisi del riconoscimento">
          <AlertTitle>Riconoscimento da ricontrollare</AlertTitle>
          <AlertDescription>
            <p>{warnings.join(" · ")}.</p>
            <p>
              {detail.entry.unknownRemoved > 0 &&
                "Le parole omesse compaiono come <unk> nel testo originale e mancano dal testo prodotto. "}
              {detail.entry.uncertainWords > 0 &&
                "Le parole incerte sono evidenziate nel testo originale, in Confronto."}
            </p>
          </AlertDescription>
        </Alert>
      )}
      <div className="flex flex-col">
        <Field data-disabled={busy || undefined}>
          <FieldLabel htmlFor="history-corrected-text">
            Testo corretto
          </FieldLabel>
          <Textarea
            id="history-corrected-text"
            value={draft}
            disabled={busy}
            onChange={(event) => {
              setDraft(event.target.value);
              onDirty(event.target.value !== detail.entry.text);
            }}
            className="min-h-40 resize-none leading-7 md:text-base"
          />
          <FieldDescription>
            Ogni salvataggio crea una revisione che puoi annullare.
          </FieldDescription>
        </Field>
        <div
          role="group"
          aria-label="Azioni della trascrizione"
          className="sticky bottom-0 flex flex-wrap items-center justify-between gap-3 border-t bg-background py-3"
        >
          <span className="text-sm text-muted-foreground" aria-live="polite">
            {dirty ? "Modifiche non salvate" : "Nessuna modifica in sospeso"}
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="ghost" disabled={busy || !dirty} onClick={discard}>
              <Undo2 data-icon="inline-start" />
              Annulla modifiche
            </Button>
            <Button
              variant="outline"
              aria-label="Copia testo salvato"
              disabled={busy}
              onClick={() => void copy()}
            >
              <Copy data-icon="inline-start" />
              Copia
            </Button>
            <ButtonGroup>
              <Button disabled={!canSave} onClick={() => void save()}>
                {busy && dirty ? (
                  <Spinner data-icon="inline-start" aria-hidden="true" />
                ) : (
                  <Save data-icon="inline-start" />
                )}
                Salva correzione
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    size="icon"
                    disabled={!canSave}
                    aria-label="Salva e ricorda per l’analisi"
                  >
                    <ChevronDown />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuLabel>
                    Salva e ricorda per l’analisi come
                  </DropdownMenuLabel>
                  <DropdownMenuGroup>
                    {correctionCategorySchema.options.map((value) => (
                      <DropdownMenuItem
                        key={value}
                        onSelect={() => void saveAndRemember(value)}
                      >
                        {categories[value]}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuGroup>
                </DropdownMenuContent>
              </DropdownMenu>
            </ButtonGroup>
          </div>
        </div>
      </div>
      <Tabs defaultValue="compare" className="gap-4">
        <TabsList>
          <TabsTrigger value="compare">Confronto</TabsTrigger>
          <TabsTrigger value="revisions">
            Revisioni
            {detail.revisions.length > 0 && (
              <Badge variant="secondary">{detail.revisions.length}</Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="details">Dettagli</TabsTrigger>
        </TabsList>
        <TabsContent value="compare" className="flex flex-col gap-5">
          <TextBlock
            title="Originale"
            description="Testo riconosciuto dal modello."
          >
            {detail.originalAsr === null ? (
              <p className="text-muted-foreground">
                Il testo originale non è disponibile per questa trascrizione.
              </p>
            ) : (
              <OriginalAsr
                utterances={detail.originalAsr}
                lowWords={detail.processing?.recognition?.lowWords ?? []}
              />
            )}
          </TextBlock>
          <TextBlock
            title="Prodotto"
            description="Dopo dizionario e pulizia, prima delle correzioni."
          >
            <p className="whitespace-pre-wrap wrap-anywhere">
              {detail.producedText}
            </p>
          </TextBlock>
          <TextBlock
            title="Corretto"
            description="Differenze rispetto al testo prodotto."
          >
            {detail.entry.text === detail.producedText ? (
              <p className="text-muted-foreground">
                Nessuna correzione salvata.
              </p>
            ) : (
              <Diff before={detail.producedText} after={detail.entry.text} />
            )}
          </TextBlock>
        </TabsContent>
        <TabsContent value="revisions" className="flex flex-col gap-4">
          {current && !dirty && (
            <section
              className="flex flex-col gap-3"
              aria-labelledby="remember-title"
            >
              <h2 id="remember-title" className="text-sm font-medium">
                Ricorda per l’analisi
                {current.category ? ` · ${categories[current.category]}` : ""}
              </h2>
              <p className="text-sm text-muted-foreground">
                Usa l’ultima correzione come esempio da rivedere nell’analisi,
                oppure annullala.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <ToggleGroup
                  type="single"
                  variant="outline"
                  value={category ?? ""}
                  disabled={busy}
                  aria-label="Categoria della correzione"
                  onValueChange={(value) =>
                    setCategory(
                      value ? correctionCategorySchema.parse(value) : null,
                    )
                  }
                >
                  {Object.entries(categories).map(([value, label]) => (
                    <ToggleGroupItem key={value} value={value}>
                      {label}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
                <Button
                  variant="outline"
                  aria-label="Ricorda questa correzione"
                  disabled={busy || !category}
                  onClick={() =>
                    category &&
                    void mutate(
                      () => historyApi.remember(id, current.id, category),
                      "Correzione ricordata.",
                    )
                  }
                >
                  <Bookmark data-icon="inline-start" />
                  Ricorda
                </Button>
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() =>
                    void mutate(
                      () => historyApi.undo(id, detail.editVersion),
                      "Ultima correzione annullata.",
                    )
                  }
                >
                  <Undo2 data-icon="inline-start" />
                  Annulla ultima correzione
                </Button>
              </div>
            </section>
          )}
          {detail.revisions.length === 0 ? (
            <Empty className="border">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <History />
                </EmptyMedia>
                <EmptyTitle>Nessuna revisione</EmptyTitle>
                <EmptyDescription>
                  Le correzioni salvate compaiono qui, dalla più recente.
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <ul className="flex flex-col gap-2" aria-label="Revisioni">
              {detail.revisions.map((revision) => (
                <li key={revision.id}>
                  <Item variant="outline" size="sm">
                    <ItemHeader className="flex-wrap justify-start text-xs text-muted-foreground">
                      <time dateTime={isoDate(revision.createdAt)}>
                        {formatDateTime(revision.createdAt)}
                      </time>
                      {revision.id === detail.currentRevision && (
                        <Badge variant="secondary">Corrente</Badge>
                      )}
                      {revision.reverted ? (
                        <Badge variant="outline">Annullata</Badge>
                      ) : revision.category ? (
                        <Badge variant="success">
                          {categories[revision.category]}
                        </Badge>
                      ) : (
                        <Badge variant="outline">Solo modifica</Badge>
                      )}
                    </ItemHeader>
                    <ItemContent>
                      <Diff
                        before={revision.beforeText}
                        after={revision.text}
                      />
                    </ItemContent>
                  </Item>
                </li>
              ))}
            </ul>
          )}
        </TabsContent>
        <TabsContent value="details" className="grid gap-6 md:grid-cols-2">
          <DetailSection title="Sessione">
            <DataList
              rows={[
                ["Data", formatDateTime(detail.entry.createdAt)],
                ["Durata", formatDuration(detail.entry.durationSeconds)],
                ["Lingua", detail.entry.language],
                ["Parole originali", String(detail.entry.wordCount)],
              ]}
            />
          </DetailSection>
          <DetailSection title="Privacy">
            <SessionPrivacy id={id} disabled={busy} />
          </DetailSection>
          {detail.processing && (
            <DetailSection title="Elaborazione" className="md:col-span-2">
              <DataList
                rows={[
                  ["Modello", detail.processing.modelId],
                  ["Revisione", detail.processing.modelRevision],
                  ["Provenienza", detail.processing.modelProvenance],
                  ["Decoder", detail.processing.decoderVersion],
                  ["Processore", detail.processing.processorVersion],
                  [
                    "Formato",
                    detail.processing.format === "faithful"
                      ? "Fedele"
                      : "Comandi vocali",
                  ],
                  [
                    "Pulizia",
                    `${detail.processing.cleanup ? "Attiva" : "Disattiva"} · ${detail.processing.cleanupVersion}`,
                  ],
                  ["Gruppi", detail.processing.groups.join(", ") || "Nessuno"],
                  [
                    "Dizionario",
                    detail.processing.dictionaryVersion ??
                      "Nessuna sostituzione",
                  ],
                  ...recognitionRows(detail.processing.recognition),
                  ...timingRows(detail.processing.timing),
                ]}
              />
            </DetailSection>
          )}
        </TabsContent>
      </Tabs>
    </>
  );
}

function TextBlock({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h2 className="text-sm font-medium">{title}</h2>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <div className="text-sm leading-7">{children}</div>
    </section>
  );
}

function DetailSection({
  title,
  className,
  children,
}: {
  title: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={className}>
      <div className="flex flex-col gap-3">
        <h2 className="text-sm font-medium">{title}</h2>
        {children}
      </div>
    </section>
  );
}

function DataList({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm wrap-anywhere sm:grid-cols-3">
      {rows.map(([label, value]) => (
        <div key={label} className="flex min-w-0 flex-col gap-0.5">
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Word-level changes: additions highlighted, removals struck through. */
function Diff({ before, after }: { before: string; after: string }) {
  return (
    <p className="text-sm leading-7 whitespace-pre-wrap wrap-anywhere">
      {diffParts(before, after).map(([kind, value], index) =>
        kind === "=" ? (
          value
        ) : kind === "-" ? (
          <del
            key={index}
            className="text-destructive decoration-destructive/60"
          >
            {value}
          </del>
        ) : (
          <ins
            key={index}
            className="rounded-sm bg-success/10 text-success no-underline"
          >
            {value}
          </ins>
        ),
      )}
    </p>
  );
}

function recognitionRows(recognition: Recognition | null): [string, string][] {
  if (!recognition) return [];
  const tags = Object.entries(recognition.languageTags)
    .sort(([, a], [, b]) => b - a)
    .map(([tag, total]) => `${tag.replace(/[<>]/g, "")} × ${total}`);
  return [
    [
      "Confidenza media",
      recognition.meanConfidence === null
        ? "Non disponibile"
        : `${Math.round(recognition.meanConfidence * 100)}% su ${recognition.scoredTokens} token`,
    ],
    [
      "Parole incerte",
      `${recognition.lowWordsTotal} sotto il ${Math.round(recognition.threshold * 100)}%`,
    ],
    ["Lingue rilevate", tags.join(", ") || "Nessun tag emesso"],
  ];
}

function timingRows(timing: Timing | null): [string, string][] {
  if (!timing) return [];
  const ms = (value: number | null) =>
    value === null ? "Non misurato" : `${value.toLocaleString("it-IT")} ms`;
  return [
    [
      "Dal tasto al microfono",
      `${ms(timing.keyToMicrophoneMs)} · decoder ${timing.preparedDecoder ? "già pronto" : "preparato all'avvio"}`,
    ],
    [
      timing.pasted ? "Dallo stop all'incolla" : "Dallo stop alla copia",
      ms(timing.stopToPasteMs),
    ],
    ["Inferenza", `${ms(timing.inferenceMs)} in ${timing.chunks} chunk`],
    ["Chiusure per silenzio", String(timing.silenceClosures)],
  ];
}

/** Il testo originale parola per parola, con le parole incerte evidenziate. */
function OriginalAsr({
  utterances,
  lowWords,
}: {
  utterances: string[];
  lowWords: Recognition["lowWords"];
}) {
  const low = new Set(lowWords.map((p) => `${p.utterance}:${p.word}`));
  return (
    <p className="whitespace-pre-wrap wrap-anywhere">
      {utterances.map((utterance, index) => {
        let word = -1;
        return (
          <span key={index}>
            {index > 0 && "\n"}
            {utterance.split(/(\s+)/).map((part, position) => {
              if (part.trim() === "") return part;
              word += 1;
              return low.has(`${index}:${word}`) ? (
                <mark
                  key={position}
                  title="Parola incerta"
                  className="rounded-sm bg-warning/20 px-0.5 text-foreground"
                >
                  {part}
                </mark>
              ) : (
                part
              );
            })}
          </span>
        );
      })}
    </p>
  );
}
