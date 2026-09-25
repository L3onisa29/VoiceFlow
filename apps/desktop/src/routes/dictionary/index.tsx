import { useRef, useState } from "react";
import { createRoute } from "@tanstack/react-router";
import { BookOpen, Plus, Pencil, Trash2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { Input } from "@/components/ui/input";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Empty,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
  EmptyDescription,
  EmptyContent,
} from "@/components/ui/empty";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import { TermDialog } from "@/components/dictionary/term-dialog";
import { GroupDialog } from "@/components/dictionary/group-dialog";
import {
  DeleteDialog,
  type DeleteTarget,
} from "@/components/dictionary/delete-dialog";
import { DictionaryError } from "@/components/dictionary/dictionary-error";
import { useDictionary } from "@/hooks/use-dictionary";
import {
  dictionaryApi,
  type TermInput,
  type GroupInput,
} from "@/lib/dictionary";
import { Route as rootRoute } from "../__root";

export const Route = createRoute({
  getParentRoute: () => rootRoute,
  path: "/dictionary",
  component: DictionaryPage,
});

export function DictionaryPage() {
  const { data, loading, busy, error, refresh, mutate, clearError } =
    useDictionary();
  const opener = useRef<HTMLElement | null>(null);
  const addButton = useRef<HTMLButtonElement | null>(null);
  const rememberFocus = () => {
    opener.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
  };
  const returnFocus = () => {
    (opener.current?.isConnected ? opener.current : addButton.current)?.focus();
  };
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [term, setTerm] = useState<TermInput | null>(null);
  const [group, setGroup] = useState<GroupInput | null>(null);
  const [deletion, setDeletion] = useState<DeleteTarget | null>(null);
  const selectedGroup = data?.groups.find((item) => String(item.id) === filter);
  const effectiveFilter =
    filter === "ungrouped" || selectedGroup ? filter : "all";
  const query = search.trim().toLowerCase();
  const terms =
    data?.terms.filter(
      (item) =>
        item.text.toLowerCase().includes(query) &&
        (effectiveFilter === "all" ||
          (effectiveFilter === "ungrouped"
            ? !item.groupIds.length
            : item.groupIds.includes(Number(effectiveFilter)))),
    ) ?? [];
  const counts = new Map<number, number>();
  for (const item of data?.terms ?? [])
    for (const id of item.groupIds) counts.set(id, (counts.get(id) ?? 0) + 1);
  const groupNames = new Map(data?.groups.map((item) => [item.id, item.name]));
  const locked = loading || busy || !data;
  const openTerm = (initial: TermInput) => {
    rememberFocus();
    clearError();
    setTerm(initial);
  };
  const addTerm = () =>
    openTerm({
      id: null,
      text: "",
      groupIds: selectedGroup ? [selectedGroup.id] : [],
    });
  const openGroup = (initial: GroupInput) => {
    rememberFocus();
    clearError();
    setGroup(initial);
  };
  const openDelete = (target: DeleteTarget) => {
    rememberFocus();
    clearError();
    setDeletion(target);
  };
  const closeDialog = () => {
    setTerm(null);
    setGroup(null);
    setDeletion(null);
    clearError();
  };
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">LE PAROLE CHE USI</p>
          <h1>Dizionario</h1>
          <p>Nomi, termini ed espressioni. Organizzati come lavori tu.</p>
        </div>
        <Button ref={addButton} onClick={addTerm} disabled={locked}>
          <Plus data-icon="inline-start" />
          Aggiungi parola
        </Button>
      </header>
      <Alert>
        <BookOpen aria-hidden="true" />
        <AlertDescription>
          Le parole salvate non modificano ancora la dettatura.
        </AlertDescription>
      </Alert>
      {error && !term && !group && !deletion && (
        <DictionaryError error={error} />
      )}
      {!data ? (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              {loading ? (
                <Spinner aria-label="Caricamento dizionario" />
              ) : (
                <BookOpen />
              )}
            </EmptyMedia>
            <EmptyTitle>
              {loading
                ? "Apertura del dizionario…"
                : "Impossibile aprire il dizionario"}
            </EmptyTitle>
            <EmptyDescription>
              {loading
                ? "Caricamento delle parole salvate sul Mac."
                : "I dati esistenti non vengono sostituiti. Puoi riprovare ad aprirli."}
            </EmptyDescription>
          </EmptyHeader>
          {!loading && (
            <EmptyContent>
              <Button variant="outline" onClick={() => void refresh()}>
                Riprova
              </Button>
            </EmptyContent>
          )}
        </Empty>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2>Parole salvate</h2>
            </CardTitle>
            <CardDescription>
              Cerca nel dizionario o seleziona un gruppo.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex min-w-0 flex-col gap-4">
            <FieldGroup className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor="dictionary-search">
                  Cerca parole
                </FieldLabel>
                <Input
                  id="dictionary-search"
                  type="search"
                  placeholder="Cerca nel dizionario…"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="dictionary-filter">Gruppo</FieldLabel>
                <Select value={effectiveFilter} onValueChange={setFilter}>
                  <SelectTrigger
                    id="dictionary-filter"
                    className="w-full min-w-0"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectItem value="all">
                        Tutte le parole ({data.terms.length})
                      </SelectItem>
                      <SelectItem value="ungrouped">
                        Senza gruppo (
                        {
                          data.terms.filter((item) => !item.groupIds.length)
                            .length
                        }
                        )
                      </SelectItem>
                      {data.groups.map((item) => (
                        <SelectItem key={item.id} value={String(item.id)}>
                          {item.name} ({counts.get(item.id) ?? 0})
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
            </FieldGroup>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm text-muted-foreground" role="status">
                {terms.length} {terms.length === 1 ? "parola" : "parole"}
                {query ? " trovate" : ""}
              </p>
              <div className="flex flex-wrap gap-1">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={locked}
                  onClick={() => openGroup({ id: null, name: "" })}
                >
                  <Plus data-icon="inline-start" />
                  Crea gruppo
                </Button>
                {selectedGroup && (
                  <>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={locked}
                      onClick={() => openGroup(selectedGroup)}
                      aria-label={`Rinomina gruppo ${selectedGroup.name}`}
                    >
                      <Pencil data-icon="inline-start" />
                      Rinomina
                    </Button>
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      disabled={locked}
                      onClick={() =>
                        openDelete({
                          kind: "group",
                          id: selectedGroup.id,
                          label: selectedGroup.name,
                        })
                      }
                      aria-label={`Elimina gruppo ${selectedGroup.name}`}
                    >
                      <Trash2 />
                    </Button>
                  </>
                )}
                <Button
                  size="icon-sm"
                  variant="ghost"
                  disabled={loading || busy}
                  onClick={() => void refresh()}
                  aria-label="Aggiorna dizionario"
                >
                  <RefreshCw />
                </Button>
              </div>
            </div>
            {loading && (
              <p
                className="flex items-center gap-2 text-sm text-muted-foreground"
                role="status"
              >
                <Spinner aria-hidden="true" /> Aggiornamento…
              </p>
            )}
            {terms.length ? (
              <div className="min-w-0 overflow-hidden rounded-lg border">
                <Table className="table-fixed">
                  <TableHeader>
                    <TableRow>
                      <TableHead>Parola</TableHead>
                      <TableHead>Gruppi</TableHead>
                      <TableHead className="w-20">
                        <span className="sr-only">Azioni</span>
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {terms.map((item) => (
                      <TableRow key={item.id}>
                        <TableCell className="max-w-56 whitespace-normal break-words">
                          {item.text}
                        </TableCell>
                        <TableCell className="max-w-64 whitespace-normal">
                          <div className="flex flex-wrap gap-1">
                            {item.groupIds.length ? (
                              item.groupIds.map((id) => (
                                <Badge
                                  variant="secondary"
                                  className="max-w-full whitespace-normal break-words"
                                  key={id}
                                >
                                  {groupNames.get(id)}
                                </Badge>
                              ))
                            ) : (
                              <span className="text-muted-foreground">
                                Senza gruppo
                              </span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="flex gap-1">
                            <Button
                              size="icon-sm"
                              variant="ghost"
                              disabled={locked}
                              aria-label={`Modifica ${item.text}`}
                              onClick={() => openTerm(item)}
                            >
                              <Pencil />
                            </Button>
                            <Button
                              size="icon-sm"
                              variant="ghost"
                              disabled={locked}
                              aria-label={`Elimina ${item.text}`}
                              onClick={() =>
                                openDelete({
                                  kind: "term",
                                  id: item.id,
                                  label: item.text,
                                })
                              }
                            >
                              <Trash2 />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <Empty className="min-h-52 border">
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <BookOpen />
                  </EmptyMedia>
                  <EmptyTitle>
                    {query
                      ? "Nessuna parola trovata"
                      : effectiveFilter !== "all"
                        ? "Qui non ci sono ancora parole"
                        : "Il tuo dizionario inizia qui"}
                  </EmptyTitle>
                  <EmptyDescription>
                    {query
                      ? "Prova un altro termine o cambia il gruppo selezionato."
                      : "Aggiungi nomi e termini che vuoi conservare. Puoi raccoglierli in più gruppi."}
                  </EmptyDescription>
                </EmptyHeader>
                <EmptyContent>
                  {query ? (
                    <Button
                      variant="outline"
                      onClick={() => {
                        setSearch("");
                        setFilter("all");
                      }}
                    >
                      Mostra tutte le parole
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      onClick={addTerm}
                      disabled={locked}
                    >
                      Aggiungi la prima parola
                    </Button>
                  )}
                </EmptyContent>
              </Empty>
            )}
          </CardContent>
          <CardFooter>
            <p className="text-xs text-muted-foreground">
              Salvato solo su questo Mac · {data.groups.length}{" "}
              {data.groups.length === 1 ? "gruppo" : "gruppi"}
            </p>
          </CardFooter>
        </Card>
      )}
      {term && data && (
        <TermDialog
          initial={term}
          groups={data.groups}
          busy={busy}
          error={error}
          onClose={closeDialog}
          onReturnFocus={returnFocus}
          onSave={(input) => {
            void mutate(() => dictionaryApi.saveTerm(input)).then((saved) => {
              if (saved) closeDialog();
            });
          }}
        />
      )}
      {group && (
        <GroupDialog
          initial={group}
          busy={busy}
          error={error}
          onClose={closeDialog}
          onReturnFocus={returnFocus}
          onSave={(input) => {
            void mutate(() => dictionaryApi.saveGroup(input)).then((saved) => {
              if (saved) closeDialog();
            });
          }}
        />
      )}
      {deletion && (
        <DeleteDialog
          target={deletion}
          busy={busy}
          error={error}
          onClose={closeDialog}
          onReturnFocus={returnFocus}
          onConfirm={() => {
            void mutate(() =>
              deletion.kind === "term"
                ? dictionaryApi.deleteTerm(deletion.id)
                : dictionaryApi.deleteGroup(deletion.id),
            ).then((saved) => {
              if (saved) closeDialog();
            });
          }}
        />
      )}
    </>
  );
}
