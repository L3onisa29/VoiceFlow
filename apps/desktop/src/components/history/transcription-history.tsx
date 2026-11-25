import { queryClient, queryKeys, invalidatePrivacy } from "@/lib/query-client";
import { useId, useRef, useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import {
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  Copy,
  History,
  MoreHorizontal,
  RotateCcw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Empty,
  EmptyContent,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
  EmptyDescription,
} from "@/components/ui/empty";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import { Item, ItemContent } from "@/components/ui/item";
import { Kbd } from "@/components/ui/kbd";
import { Skeleton } from "@/components/ui/skeleton";
import { useHistory } from "@/hooks/use-history";
import { useShortcut } from "@/hooks/use-shortcut";
import {
  formatDuration,
  historyApi,
  recognitionWarnings,
  type HistoryEntry,
} from "@/lib/history";
import { toUserMessage } from "@/lib/errors";
import { copyTranscription } from "./actions";
import {
  formatDateTime,
  formatTime,
  formatWords,
  groupByDay,
  isoDate,
} from "./format";

export function TranscriptionHistory({
  saveError,
}: {
  saveError: string | null;
}) {
  const [search, setSearch] = useState("");
  const searchRef = useRef(search);
  searchRef.current = search;
  const searchInput = useRef<HTMLInputElement>(null);
  const { pages, hasNextPage, loading, error, refresh, loadMore } =
    useHistory(search);
  const [pageIndex, setPageIndex] = useState(0);
  const currentPage = pages ? Math.min(pageIndex, pages.length - 1) : 0;
  const data = pages?.[currentPage];
  const [deletion, setDeletion] = useState<HistoryEntry | null>(null);
  const [busy, setBusy] = useState(false);
  const opener = useRef<number | null>(null);
  const searching = search.trim().length > 0;
  useShortcut(
    "mod+f",
    () => {
      searchInput.current?.focus();
      searchInput.current?.select();
    },
    { allowInInputs: true },
  );
  // Back to the row menu that opened the dialog, or to the search field.
  const returnFocus = () =>
    (
      (opener.current === null
        ? null
        : document.querySelector<HTMLElement>(
            `[data-history-actions="${opener.current}"]`,
          )) ?? searchInput.current
    )?.focus();
  function updateSearch(value: string) {
    setSearch(value);
    setPageIndex(0);
  }
  async function copy(id: number) {
    setBusy(true);
    await copyTranscription(id);
    setBusy(false);
  }
  async function remove() {
    if (!deletion || busy) return;
    setBusy(true);
    try {
      await historyApi.delete(deletion.id);
      // Drop deleted text from inactive searches as well as the visible page.
      await queryClient.resetQueries({ queryKey: queryKeys.history });
      await invalidatePrivacy();
      opener.current = null;
      setDeletion(null);
      if (data?.entries.length === 1 && currentPage > 0)
        setPageIndex(currentPage - 1);
      refresh();
      toast.success("Trascrizione eliminata.");
    } catch (error) {
      toast.error(toUserMessage(error));
    } finally {
      setBusy(false);
    }
  }
  async function nextPage() {
    if (!pages) return;
    const requestedSearch = search;
    if (currentPage + 1 < pages.length) {
      setPageIndex(currentPage + 1);
    } else if (
      hasNextPage &&
      (await loadMore()) &&
      searchRef.current === requestedSearch
    ) {
      setPageIndex(currentPage + 1);
    }
  }
  const paginated =
    data && (currentPage > 0 || (pages?.length ?? 0) > 1 || hasNextPage);
  return (
    <>
      <InputGroup>
        <InputGroupAddon>
          <Search />
        </InputGroupAddon>
        <InputGroupInput
          ref={searchInput}
          type="search"
          aria-label="Cerca nelle trascrizioni"
          placeholder="Cerca una parola o una frase"
          value={search}
          maxLength={200}
          onChange={(event) => updateSearch(event.target.value)}
        />
        <InputGroupAddon align="inline-end">
          {searching ? (
            <InputGroupButton
              size="icon-xs"
              aria-label="Cancella ricerca"
              onClick={() => {
                updateSearch("");
                searchInput.current?.focus();
              }}
            >
              <X />
            </InputGroupButton>
          ) : (
            <Kbd>⌘F</Kbd>
          )}
        </InputGroupAddon>
      </InputGroup>
      {saveError && (
        <Alert variant="destructive">
          <AlertTitle>Salvataggio non riuscito</AlertTitle>
          <AlertDescription>{saveError}</AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Storico non disponibile</AlertTitle>
          <AlertDescription className="flex flex-col items-start gap-2">
            <span>{error}</span>
            <Button variant="outline" size="sm" onClick={refresh}>
              <RotateCcw data-icon="inline-start" />
              Riprova
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <section
        aria-label="Trascrizioni"
        aria-busy={loading}
        className="flex flex-col gap-6"
      >
        {data && data.entries.length > 0 ? (
          groupByDay(data.entries).map((group) => (
            <DayGroup key={group.key} label={group.label}>
              {group.entries.map((entry) => (
                <HistoryRow
                  key={entry.id}
                  entry={entry}
                  disabled={busy}
                  onCopy={() => void copy(entry.id)}
                  onDelete={() => {
                    opener.current = entry.id;
                    setDeletion(entry);
                  }}
                />
              ))}
            </DayGroup>
          ))
        ) : !data && loading ? (
          <HistorySkeleton />
        ) : (
          !error && (
            <Empty className="border">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  {searching ? <Search /> : <History />}
                </EmptyMedia>
                <EmptyTitle>
                  {searching
                    ? "Nessun risultato"
                    : "Nessuna trascrizione salvata"}
                </EmptyTitle>
                <EmptyDescription>
                  {searching
                    ? "Nessuna trascrizione contiene questo testo."
                    : "Le dettature concluse vengono salvate qui. Quelle annullate o senza testo non vengono conservate."}
                </EmptyDescription>
              </EmptyHeader>
              <EmptyContent>
                {searching ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      updateSearch("");
                      searchInput.current?.focus();
                    }}
                  >
                    Cancella ricerca
                  </Button>
                ) : (
                  <Button asChild variant="outline" size="sm">
                    <Link to="/">Vai alla dettatura</Link>
                  </Button>
                )}
              </EmptyContent>
            </Empty>
          )
        )}
        {paginated && (
          <nav
            className="flex items-center justify-end gap-2"
            aria-label="Pagine dello storico"
          >
            <Button
              size="icon-sm"
              variant="outline"
              disabled={loading || currentPage === 0}
              aria-label="Pagina precedente"
              title="Pagina precedente"
              onClick={() => setPageIndex(currentPage - 1)}
            >
              <ChevronLeft />
            </Button>
            <span
              className="min-w-16 text-center text-xs tabular-nums"
              aria-live="polite"
            >
              Pagina {currentPage + 1}
            </span>
            <Button
              size="icon-sm"
              variant="outline"
              disabled={
                loading ||
                (!hasNextPage && currentPage + 1 >= (pages?.length ?? 0))
              }
              aria-label="Pagina successiva"
              title="Pagina successiva"
              onClick={() => void nextPage()}
            >
              <ChevronRight />
            </Button>
          </nav>
        )}
      </section>
      <AlertDialog
        open={deletion !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setDeletion(null);
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
              restano regole indipendenti. Eventuali testi già inviati a servizi
              esterni non vengono cancellati da quei servizi. L'operazione non
              può essere annullata.
            </AlertDialogDescription>
          </AlertDialogHeader>
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
    </>
  );
}

function DayGroup({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  return (
    <div role="group" aria-labelledby={id} className="flex flex-col gap-1">
      <h2 id={id} className="px-3 text-xs font-medium text-muted-foreground">
        {label}
      </h2>
      <ul className="flex flex-col">{children}</ul>
    </div>
  );
}

function HistoryRow({
  entry,
  disabled,
  onCopy,
  onDelete,
}: {
  entry: HistoryEntry;
  disabled: boolean;
  onCopy: () => void;
  onDelete: () => void;
}) {
  const when = formatDateTime(entry.createdAt);
  return (
    <li className="flex min-w-0 items-center gap-1">
      <Item asChild className="min-w-0 flex-1">
        <Link
          to="/history/$transcriptionId"
          params={{ transcriptionId: entry.id }}
        >
          <ItemContent className="min-w-0">
            <p className="line-clamp-2 whitespace-pre-wrap wrap-anywhere text-sm leading-relaxed">
              {entry.text}
            </p>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
              <time
                dateTime={isoDate(entry.createdAt)}
                className="tabular-nums"
              >
                {formatTime(entry.createdAt)}
              </time>
              <span aria-hidden="true">·</span>
              <span>{formatDuration(entry.durationSeconds)}</span>
              <span aria-hidden="true">·</span>
              <span>{formatWords(entry.wordCount)}</span>
              {recognitionWarnings(entry).map((warning) => (
                <Badge key={warning} variant="warning">
                  {warning}
                </Badge>
              ))}
            </div>
          </ItemContent>
        </Link>
      </Item>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button
            data-history-actions={entry.id}
            variant="ghost"
            size="icon-sm"
            disabled={disabled}
            aria-label={`Azioni per la trascrizione del ${when}`}
          >
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuGroup>
            <DropdownMenuItem onSelect={onCopy}>
              <Copy />
              Copia
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link
                to="/history/$transcriptionId"
                params={{ transcriptionId: entry.id }}
              >
                <ArrowUpRight />
                Apri
              </Link>
            </DropdownMenuItem>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem variant="destructive" onSelect={onDelete}>
              <Trash2 />
              Elimina
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  );
}

function HistorySkeleton() {
  return (
    <div className="flex flex-col gap-1" aria-hidden="true">
      <Skeleton className="mx-3 h-3 w-16" />
      {Array.from({ length: 4 }, (_, index) => (
        <div key={index} className="flex flex-col gap-2 px-3 py-2.5">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-3 w-40" />
        </div>
      ))}
    </div>
  );
}
