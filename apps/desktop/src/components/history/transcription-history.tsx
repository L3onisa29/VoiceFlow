import { useRef, useState } from "react";
import { Check, Copy, History, RefreshCw, Trash2 } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
  CardAction,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Empty,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
  EmptyDescription,
} from "@/components/ui/empty";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { HistoryEditor } from "@/components/history/history-editor";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useHistory } from "@/hooks/use-history";
import { formatDuration, historyApi, type HistoryEntry } from "@/lib/history";

const number = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 0 });
const date = new Intl.DateTimeFormat("it-IT", {
  dateStyle: "medium",
  timeStyle: "short",
});

export function TranscriptionHistory({
  saveError,
}: {
  saveError: string | null;
}) {
  const [search, setSearch] = useState("");
  const { data, loading, error, refresh, loadMore } = useHistory(search);
  const [selected, setSelected] = useState<HistoryEntry | null>(null);
  const [deletion, setDeletion] = useState<HistoryEntry | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const rememberFocus = () => {
    opener.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
  };
  const returnFocus = () =>
    (opener.current?.isConnected ? opener.current : heading.current)?.focus();
  async function copy(id: number) {
    setBusy(true);
    setActionError(null);
    setCopied(null);
    try {
      await historyApi.copy(id);
      setCopied(id);
    } catch (error) {
      setActionError(String(error));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!deletion || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      await historyApi.delete(deletion.id);
      opener.current = null;
      setDeletion(null);
      heading.current?.focus();
      refresh();
    } catch (error) {
      setActionError(String(error));
    } finally {
      setBusy(false);
    }
  }
  const stats = data?.stats;
  const metrics = [
    {
      label: "Parole totali",
      value: stats ? number.format(stats.totalWords) : null,
    },
    {
      label: "Dettature",
      value: stats ? number.format(stats.totalSessions) : null,
    },
    {
      label: "Tempo di dettatura",
      value: stats ? formatDuration(stats.totalSeconds) : null,
    },
    {
      label: "Parole al minuto",
      value: stats
        ? stats.wordsPerMinute === null
          ? "Non disponibile"
          : number.format(stats.wordsPerMinute)
        : null,
    },
  ];
  return (
    <section className="flex flex-col gap-4" aria-labelledby="history-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2
            id="history-title"
            ref={heading}
            tabIndex={-1}
            className="text-lg font-semibold"
          >
            Le tue trascrizioni
          </h2>
          <p className="text-sm text-muted-foreground">
            Il tuo storico, salvato solo su questo Mac.
          </p>
        </div>
        <Badge variant="outline">Archivio locale</Badge>
      </div>
      {saveError && (
        <Alert variant="destructive">
          <AlertTitle>Salvataggio non riuscito</AlertTitle>
          <AlertDescription>{saveError}</AlertDescription>
        </Alert>
      )}
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {metrics.map(({ label, value }) => (
          <Card key={label} size="sm">
            <CardHeader>
              <CardDescription>{label}</CardDescription>
              <CardTitle>
                {value === null ? (
                  loading ? (
                    <Skeleton className="h-6 w-16" />
                  ) : (
                    "Non disponibile"
                  )
                ) : (
                  value
                )}
              </CardTitle>
            </CardHeader>
          </Card>
        ))}
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        Statistiche su tutto lo storico conservato. La velocità usa il tempo di
        registrazione, silenzi inclusi e pause escluse.
      </p>
      <Card>
        <CardHeader>
          <CardTitle>Storico</CardTitle>
          <CardDescription>
            Le dettature più recenti compaiono per prime.
          </CardDescription>
          <CardAction>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Aggiorna storico"
              onClick={refresh}
              disabled={loading}
            >
              <RefreshCw />
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent className="flex min-w-0 flex-col gap-4">
          <Field>
            <FieldLabel htmlFor="history-search">
              Cerca nelle trascrizioni
            </FieldLabel>
            <Input
              id="history-search"
              type="search"
              placeholder="Cerca una parola o una frase…"
              value={search}
              maxLength={200}
              onChange={(event) => setSearch(event.target.value)}
            />
          </Field>
          {(error || (actionError && !deletion && !selected)) && (
            <Alert variant="destructive">
              <AlertDescription>{error || actionError}</AlertDescription>
              <Button
                variant="outline"
                size="sm"
                className="mt-2 justify-self-start"
                onClick={() => {
                  setActionError(null);
                  refresh();
                }}
              >
                Riprova
              </Button>
            </Alert>
          )}
          {loading && (
            <p
              role="status"
              className="flex items-center gap-2 text-sm text-muted-foreground"
            >
              <Spinner aria-hidden="true" />
              Caricamento storico…
            </p>
          )}
          {data && data.entries.length > 0 ? (
            <ul className="flex flex-col divide-y" aria-busy={loading}>
              {data.entries.map((entry) => (
                <li
                  key={entry.id}
                  className="flex min-w-0 flex-col gap-3 py-4 first:pt-0 last:pb-0"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                    <time dateTime={new Date(entry.createdAt).toISOString()}>
                      {date.format(entry.createdAt)}
                    </time>
                    <span>
                      {number.format(entry.wordCount)} parole ·{" "}
                      {formatDuration(entry.durationSeconds)}
                    </span>
                  </div>
                  <p className="line-clamp-3 whitespace-pre-wrap wrap-anywhere text-sm leading-relaxed">
                    {entry.text}
                  </p>
                  <div className="flex flex-wrap items-center gap-1">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        rememberFocus();
                        setActionError(null);
                        setSelected(entry);
                      }}
                    >
                      Apri testo
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      aria-label={`Copia trascrizione del ${date.format(entry.createdAt)}`}
                      onClick={() => void copy(entry.id)}
                    >
                      {copied === entry.id ? (
                        <Check data-icon="inline-start" />
                      ) : (
                        <Copy data-icon="inline-start" />
                      )}
                      {copied === entry.id ? "Copiato" : "Copia"}
                    </Button>
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      disabled={busy}
                      aria-label={`Elimina trascrizione del ${date.format(entry.createdAt)}`}
                      onClick={() => {
                        rememberFocus();
                        setActionError(null);
                        setDeletion(entry);
                      }}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            !loading &&
            !error && (
              <Empty>
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <History />
                  </EmptyMedia>
                  <EmptyTitle>
                    {search.trim()
                      ? "Nessuna trascrizione trovata"
                      : "Il tuo storico inizia qui"}
                  </EmptyTitle>
                  <EmptyDescription>
                    {search.trim()
                      ? "Prova un'altra parola o cancella la ricerca."
                      : "Le prossime dettature concluse verranno salvate qui. Le dettature annullate e quelle senza testo non vengono conservate."}
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            )
          )}
        </CardContent>
        <CardFooter className="flex-wrap justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            Solo testo e dati della sessione. Nessun audio salvato.
          </p>
          {data?.nextCursor && (
            <Button
              variant="outline"
              size="sm"
              disabled={loading}
              onClick={() => void loadMore()}
            >
              Carica altre
            </Button>
          )}
        </CardFooter>
      </Card>
      <Dialog
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open && !busy) {
            setSelected(null);
            setActionError(null);
          }
        }}
      >
        <DialogContent
          className="sm:max-w-2xl"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            returnFocus();
          }}
        >
          <DialogHeader>
            <DialogTitle>Trascrizione salvata</DialogTitle>
            <DialogDescription>
              {selected &&
                `${date.format(selected.createdAt)} · ${selected.wordCount} parole · ${formatDuration(selected.durationSeconds)}`}
            </DialogDescription>
          </DialogHeader>
          {selected && (
            <HistoryEditor
              key={selected.id}
              id={selected.id}
              onChanged={refresh}
              onBusy={setBusy}
            />
          )}
        </DialogContent>
      </Dialog>
      <AlertDialog
        open={deletion !== null}
        onOpenChange={(open) => {
          if (!open && !busy) {
            setDeletion(null);
            setActionError(null);
          }
        }}
      >
        <AlertDialogContent
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            returnFocus();
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Eliminare la trascrizione?</AlertDialogTitle>
            <AlertDialogDescription>
              Originale, risultato, revisioni ed esempi ricordati verranno
              rimossi da questo archivio locale insieme ai conteggi. Anche le
              proposte non approvate e i riferimenti sorgente saranno rimossi.
              Le definizioni già approvate, con i loro esempi e versioni,
              restano regole indipendenti. Eventuali
              testi già inviati a servizi esterni non vengono cancellati da quei
              servizi. L'operazione non può essere annullata.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {actionError && (
            <Alert variant="destructive">
              <AlertDescription>{actionError}</AlertDescription>
            </Alert>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Annulla</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={busy}
              onClick={(event) => {
                event.preventDefault();
                void remove();
              }}
            >
              {busy ? "Eliminazione…" : "Elimina"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
