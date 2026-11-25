import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { listen } from "@tauri-apps/api/event";
import {
  ChevronLeft,
  ChevronRight,
  CircleCheck,
  FileText,
  Mic,
  RotateCcw,
  SkipForward,
  Square,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import {
  Page,
  PageContent,
  PageHeader,
  PageSection,
} from "@/components/layout/page";
import { SettingsAdvanced } from "@/components/settings/settings-advanced";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Item, ItemContent, ItemDescription } from "@/components/ui/item";
import { Kbd } from "@/components/ui/kbd";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useDictationState } from "@/hooks/use-dictation";
import { useShortcut } from "@/hooks/use-shortcut";
import { toUserMessage } from "@/lib/errors";
import {
  levelPercent,
  nextPending,
  profileCounts,
  profileDuration,
  profileEventSchema,
  voiceProfileApi,
  voiceProfileKey,
} from "@/lib/voice-profile";

export const Route = createFileRoute("/voice-profile/")({
  component: VoiceProfilePage,
});

/** The focused button, which keeps Space as its own activation key. */
const focusedButton = (target: EventTarget | null) =>
  target instanceof Element
    ? target.closest<HTMLElement>('button, [role="button"]')
    : null;

function VoiceProfilePage() {
  const client = useQueryClient();
  const engine = useDictationState("phase", "modelReady");
  const {
    data,
    error: readError,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: voiceProfileKey,
    queryFn: voiceProfileApi.status,
    staleTime: 0,
  });
  const [position, setPosition] = useState<number | null>(null);
  const [live, setLive] = useState<{ seconds: number; level: number } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  // Between "stop" and the worker's answer the model is still listening to the take.
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    let disposed = false;
    let release: (() => void) | undefined;
    void listen("voice-profile", (event) => {
      if (disposed) return;
      const parsed = profileEventSchema.safeParse(event.payload);
      if (!parsed.success) return;
      if (parsed.data.kind === "progress") {
        setLive({ seconds: parsed.data.seconds, level: parsed.data.level });
        return;
      }
      setLive(null);
      setSaving(false);
      if (parsed.data.kind === "error")
        toast.error(toUserMessage(parsed.data.message));
      if (parsed.data.kind === "saved") setPosition(null);
      void client.invalidateQueries({ queryKey: voiceProfileKey });
    }).then((unlisten) => {
      if (disposed) unlisten();
      else release = unlisten;
    });
    return () => {
      disposed = true;
      release?.();
    };
  }, [client]);

  const prompts = data?.prompts ?? [];
  // Unless the person navigated, show the first sentence still to be read.
  const index =
    prompts.length === 0
      ? null
      : Math.min(position ?? nextPending(prompts, 0) ?? 0, prompts.length - 1);
  const prompt = index === null ? null : prompts[index];
  const recording = data?.recording ?? null;
  const isRecording = prompt !== null && recording === prompt.id;
  const dictating = !["idle", "complete", "error"].includes(
    engine?.phase ?? "idle",
  );
  const blocked = busy || recording !== null || dictating;
  const canRecord = prompt !== null && !blocked && !!engine?.modelReady;
  const canStop = isRecording && !busy && !saving;
  const complete = prompts.length > 0 && nextPending(prompts, 0) === null;
  const counts = profileCounts(prompts);

  async function run(job: () => Promise<unknown>) {
    setBusy(true);
    try {
      await job();
      await client.invalidateQueries({ queryKey: voiceProfileKey });
      return true;
    } catch (reason) {
      toast.error(toUserMessage(reason));
      return false;
    } finally {
      setBusy(false);
    }
  }
  const move = (step: number) => {
    if (index !== null)
      setPosition((index + step + prompts.length) % prompts.length);
  };
  const start = () => {
    if (!prompt || !canRecord) return;
    setPosition(index);
    void run(() => voiceProfileApi.start(prompt.id));
  };
  const stop = () => {
    if (!canStop) return;
    setSaving(true);
    void run(voiceProfileApi.stop).then((ok) => {
      if (!ok) setSaving(false);
    });
  };

  const shortcuts = { enabled: !!data?.available && !confirmDelete };
  useShortcut(
    " ",
    (event) => {
      // useShortcut already prevented the default: activate the control itself.
      const control = focusedButton(event.target);
      if (control) control.click();
      else if (isRecording) stop();
      else start();
    },
    shortcuts,
  );
  useShortcut("arrowleft", () => !blocked && move(-1), shortcuts);
  useShortcut("arrowright", () => !blocked && move(1), shortcuts);

  return (
    <Page>
      <PageHeader
        title="Profilo vocale"
        description="Leggi una frase alla volta. Audio e testo restano su questo Mac e l'audio non viene mai riprodotto."
      >
        {data?.available && prompts.length > 0 && (
          <section
            aria-label="Avanzamento del profilo"
            className="flex flex-col gap-2"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
              <p className="font-medium">
                {data.recorded} di {counts.total} frasi registrate ·{" "}
                {profileDuration(data.seconds)} di audio
              </p>
              <p className="text-muted-foreground">
                {data.recordedTest} di {counts.test} di prova · {counts.skipped}{" "}
                saltate
              </p>
            </div>
            <Progress
              aria-label="Frasi registrate"
              value={(data.recorded / counts.total) * 100}
              aria-valuemin={0}
              aria-valuemax={counts.total}
              aria-valuenow={data.recorded}
              aria-valuetext={`${data.recorded} di ${counts.total}`}
            />
          </section>
        )}
      </PageHeader>
      <PageContent>
        {readError && (
          <Alert variant="destructive">
            <AlertTitle>Profilo vocale non disponibile</AlertTitle>
            <AlertDescription>{toUserMessage(readError)}</AlertDescription>
            <Button
              className="col-start-2 mt-2 justify-self-start"
              variant="outline"
              size="sm"
              disabled={isFetching}
              onClick={() => void refetch()}
            >
              Riprova
            </Button>
          </Alert>
        )}
        {!data && !readError && <Skeleton className="h-64 w-full" />}
        {data && !data.available && (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <FileText />
              </EmptyMedia>
              <EmptyTitle>Nessuna frase da leggere</EmptyTitle>
              <EmptyDescription className="break-all">
                Metti il file <code>prompts.json</code> nella cartella{" "}
                {data.folder}. Le frasi restano fuori dal progetto perché
                contengono nomi di clienti e progetti.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        )}
        {data?.available && engine && !engine.modelReady && (
          <Alert>
            <AlertTitle>Il modello non è caricato</AlertTitle>
            <AlertDescription>
              Ogni lettura passa dal modello per misurarne l'errore di partenza.
            </AlertDescription>
            <Button
              className="col-start-2 mt-2 justify-self-start"
              variant="outline"
              size="sm"
              asChild
            >
              <Link to="/settings" search={{ section: "system" }}>
                Apri Sistema
              </Link>
            </Button>
          </Alert>
        )}
        {data?.available && complete && (
          <Alert>
            <CircleCheck />
            <AlertTitle>Profilo completo</AlertTitle>
            <AlertDescription>
              Tutte le frasi sono registrate o saltate. Puoi rifare una lettura
              o rimettere in coda quelle saltate.
            </AlertDescription>
          </Alert>
        )}
        {data?.available && prompt && index !== null && (
          <>
            <Card aria-label="Frase da leggere">
              <CardHeader>
                <CardTitle>
                  <h2>
                    Frase {index + 1} di {prompts.length}
                  </h2>
                </CardTitle>
                <CardDescription className="flex flex-wrap gap-2">
                  <Badge variant="outline">
                    {prompt.split === "test" ? "Prova" : "Addestramento"}
                  </Badge>
                </CardDescription>
                <CardAction>
                  <Badge
                    variant={
                      isRecording
                        ? "destructive"
                        : prompt.recording
                          ? "success"
                          : "secondary"
                    }
                  >
                    {isRecording
                      ? "In registrazione"
                      : prompt.recording
                        ? `Registrata · ${profileDuration(prompt.recording.duration)}`
                        : prompt.skipped
                          ? "Saltata"
                          : "Da leggere"}
                  </Badge>
                </CardAction>
              </CardHeader>
              <CardContent className="flex flex-col gap-6">
                <p
                  className="text-3xl leading-snug font-medium text-balance"
                  lang={data.language}
                >
                  {prompt.text}
                </p>
                {isRecording && (
                  <div className="flex items-center gap-3" role="status">
                    <Progress
                      role="meter"
                      aria-label="Livello microfono"
                      className="w-40"
                      value={levelPercent(live?.level ?? 0)}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={levelPercent(live?.level ?? 0)}
                    />
                    <span className="text-sm text-muted-foreground tabular-nums">
                      Registrazione · {(live?.seconds ?? 0).toFixed(1)} s
                    </span>
                  </div>
                )}
                {prompt.recording && !isRecording && (
                  <Item variant="muted" size="sm">
                    <ItemContent>
                      <ItemDescription>
                        Il modello attuale ha capito
                      </ItemDescription>
                      <p className="text-sm break-words whitespace-pre-wrap">
                        {prompt.recording.hypothesis ||
                          "Nessun parlato rilevato"}
                      </p>
                    </ItemContent>
                  </Item>
                )}
                {dictating && recording === null && (
                  <p className="text-sm text-muted-foreground">
                    Dettatura in corso: la registrazione del profilo riprende
                    quando termina.
                  </p>
                )}
              </CardContent>
              <CardFooter className="flex flex-wrap gap-2">
                {isRecording ? (
                  <>
                    <Button
                      disabled={!canStop}
                      aria-keyshortcuts="Space"
                      onClick={stop}
                    >
                      {saving ? (
                        <Spinner data-icon="inline-start" />
                      ) : (
                        <Square data-icon="inline-start" />
                      )}
                      {saving ? "Salvataggio…" : "Interrompi"}
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy || saving}
                      onClick={() => void run(voiceProfileApi.cancel)}
                    >
                      Annulla
                    </Button>
                  </>
                ) : (
                  <>
                    <Button
                      disabled={!canRecord}
                      aria-keyshortcuts="Space"
                      onClick={start}
                    >
                      {prompt.recording ? (
                        <RotateCcw data-icon="inline-start" />
                      ) : (
                        <Mic data-icon="inline-start" />
                      )}
                      {prompt.recording ? "Rifai" : "Registra"}
                    </Button>
                    {!prompt.recording && (
                      <Button
                        variant="outline"
                        disabled={blocked}
                        onClick={() =>
                          void run(async () => {
                            await voiceProfileApi.skip(
                              prompt.id,
                              !prompt.skipped,
                            );
                            setPosition(null);
                          })
                        }
                      >
                        <SkipForward data-icon="inline-start" />
                        {prompt.skipped ? "Rimetti in coda" : "Salta"}
                      </Button>
                    )}
                  </>
                )}
                <div className="ml-auto flex gap-2">
                  <Button
                    variant="outline"
                    size="icon"
                    aria-label="Frase precedente"
                    aria-keyshortcuts="ArrowLeft"
                    disabled={blocked}
                    onClick={() => move(-1)}
                  >
                    <ChevronLeft />
                  </Button>
                  <Button
                    variant="outline"
                    size="icon"
                    aria-label="Frase successiva"
                    aria-keyshortcuts="ArrowRight"
                    disabled={blocked}
                    onClick={() => move(1)}
                  >
                    <ChevronRight />
                  </Button>
                </div>
              </CardFooter>
            </Card>
            <p className="flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
              <Kbd>Spazio</Kbd> registra o interrompe · <Kbd>←</Kbd>
              <Kbd>→</Kbd> cambiano frase
            </p>
            <SettingsAdvanced label="Dettagli tecnici">
              <div className="flex flex-col gap-2 text-sm text-muted-foreground">
                <p className="break-all">Cartella: {data.folder}</p>
                <p>
                  Un file WAV per frase e i manifest per addestramento e prova.
                  Le frasi di prova non entrano nell'addestramento.
                </p>
                <p>Nessun audio delle dettature normali viene conservato.</p>
                {prompt.recording && (
                  <p>
                    Frase {prompt.id} · {prompt.recording.device} ·{" "}
                    {new Date(prompt.recording.recordedAt).toLocaleString(
                      "it-IT",
                    )}
                  </p>
                )}
              </div>
            </SettingsAdvanced>
            <PageSection
              title="Elimina profilo"
              description="Rimuove registrazioni, trascrizioni del modello e manifest da questo Mac."
              actions={
                <Button
                  variant="destructive"
                  disabled={blocked || data.recorded === 0}
                  onClick={() => setConfirmDelete(true)}
                >
                  <Trash2 data-icon="inline-start" />
                  Cancella tutto il profilo
                </Button>
              }
            />
          </>
        )}
      </PageContent>
      <AlertDialog
        open={confirmDelete}
        onOpenChange={(open) => {
          if (!open && !busy) setConfirmDelete(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancellare il profilo vocale?</AlertDialogTitle>
            <AlertDialogDescription>
              Tutte le registrazioni, le trascrizioni del modello e i manifest
              verranno eliminati da questo Mac. Le frasi da leggere restano.
              L'operazione non si può annullare.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Annulla</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={busy}
              onClick={(event) => {
                event.preventDefault();
                void run(async () => {
                  await voiceProfileApi.deleteAll();
                  setPosition(null);
                }).then((ok) => {
                  setConfirmDelete(false);
                  if (ok) toast.success("Profilo vocale cancellato");
                });
              }}
            >
              {busy && <Spinner data-icon="inline-start" />}
              Cancella tutto
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Page>
  );
}
