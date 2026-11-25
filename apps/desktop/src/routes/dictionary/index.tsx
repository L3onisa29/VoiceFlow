import { useEffect, useRef, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";
import { toast } from "sonner";
import { BookOpen, Plus, TriangleAlert } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Page, PageContent, PageHeader } from "@/components/layout/page";
import { ActiveGroupsMenu } from "@/components/dictionary/active-groups-menu";
import {
  AutoVariants,
  useAutoVariants,
} from "@/components/dictionary/auto-variants";
import {
  BulkActions,
  BulkDeleteDialog,
} from "@/components/dictionary/bulk-actions";
import {
  DeleteDialog,
  type DeleteTarget,
} from "@/components/dictionary/delete-dialog";
import { DictionaryError } from "@/components/dictionary/dictionary-error";
import { DictionaryToolbar } from "@/components/dictionary/dictionary-toolbar";
import { GroupDialog } from "@/components/dictionary/group-dialog";
import { GroupManager } from "@/components/dictionary/group-manager";
import { TermDialog } from "@/components/dictionary/term-dialog";
import { TermPagination } from "@/components/dictionary/term-pagination";
import {
  TermTable,
  TermTableSkeleton,
} from "@/components/dictionary/term-table";
import { useDictionary } from "@/hooks/use-dictionary";
import { useShortcut } from "@/hooks/use-shortcut";
import {
  applyToTerms,
  dictionaryApi,
  emptyTermRule,
  filterTerms,
  groupTermCounts,
  type BulkOutcome,
  type Dictionary,
  type Group,
  type GroupInput,
  type Term,
  type TermFilter,
  type TermInput,
  type TermSort,
} from "@/lib/dictionary";
import { toUserMessage } from "@/lib/errors";
import { dictionaryQuery, warm } from "@/lib/route-loaders";

export const Route = createFileRoute("/dictionary/")({
  // `?new=1` opens the add dialog (command palette "Aggiungi parola").
  validateSearch: z.object({ new: z.literal(1).optional().catch(undefined) }),
  loader: ({ context }) =>
    warm(context.queryClient.ensureQueryData(dictionaryQuery)),
  component: DictionaryPage,
});

const PAGE_SIZE = 10;
const words = (count: number) => (count === 1 ? "1 parola" : `${count} parole`);
const plural = (count: number, one: string, many: string) =>
  count === 1 ? one : many;

type Editor = { initial: TermInput; title?: string };

function DictionaryPage() {
  const { data, loading, busy, error, refresh, mutate, write, clearError } =
    useDictionary();
  const autoVariants = useAutoVariants(data?.version ?? null).data ?? [];
  const automatic = new Set(autoVariants.map((variant) => variant.spelling));

  const opener = useRef<HTMLElement | null>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const groupsButton = useRef<HTMLButtonElement>(null);
  const rememberFocus = (element?: HTMLElement | null) => {
    opener.current =
      element ??
      (document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null);
  };
  const returnFocus = () => {
    (opener.current?.isConnected
      ? opener.current
      : (document.querySelector<HTMLElement>('[role="dialog"] button') ??
        addButton.current)
    )?.focus();
  };

  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<TermSort>("az");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<TermFilter>("all");
  const [selection, setSelection] = useState<Set<number>>(() => new Set());
  const [editor, setEditor] = useState<Editor | null>(null);
  const [group, setGroup] = useState<GroupInput | null>(null);
  const [deletion, setDeletion] = useState<DeleteTarget | null>(null);
  const [manageGroups, setManageGroups] = useState(false);
  const [bulkDeletion, setBulkDeletion] = useState<number[] | null>(null);

  const selectedGroup = data?.groups.find((item) => String(item.id) === filter);
  const effectiveFilter: TermFilter =
    filter === "ungrouped" || selectedGroup ? filter : "all";
  const terms = data
    ? filterTerms(data.terms, search, effectiveFilter, sort)
    : [];
  const pageCount = Math.max(1, Math.ceil(terms.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);
  const visibleTerms = terms.slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE,
  );
  const counts = groupTermCounts(data?.terms ?? []);
  const groupNames = new Map(data?.groups.map((item) => [item.id, item.name]));
  // Deleted terms leave the selection as soon as the snapshot drops them.
  const selectedTerms = (data?.terms ?? []).filter((item) =>
    selection.has(item.id),
  );
  const selected = new Set(selectedTerms.map((item) => item.id));
  const locked = loading || busy || !data;
  const dialogOpen =
    !!editor || !!group || !!deletion || manageGroups || !!bulkDeletion;

  const resetList = () => {
    setPage(1);
    setSelection(new Set());
  };
  const openEditor = (next: Editor, element?: HTMLElement | null) => {
    rememberFocus(element);
    clearError();
    setEditor(next);
  };
  const addTerm = () =>
    openEditor({
      initial: {
        ...emptyTermRule,
        id: null,
        text: "",
        groupIds: selectedGroup ? [selectedGroup.id] : [],
      },
    });
  const openGroup = (initial: GroupInput) => {
    rememberFocus();
    clearError();
    setGroup(initial);
  };
  const openDelete = (target: DeleteTarget, element?: HTMLElement | null) => {
    rememberFocus(element);
    clearError();
    setDeletion(target);
  };
  const closeDialog = () => {
    setEditor(null);
    setGroup(null);
    setDeletion(null);
    clearError();
  };

  useShortcut(
    "mod+f",
    () => {
      searchInput.current?.focus();
      searchInput.current?.select();
    },
    { enabled: !!data && !dialogOpen, allowInInputs: true },
  );
  useShortcut("mod+n", addTerm, { enabled: !locked && !dialogOpen });

  const { new: requestedNew } = Route.useSearch();
  const navigate = Route.useNavigate();
  // Open the add dialog once the dictionary is ready, then drop the request.
  useEffect(() => {
    if (!requestedNew || locked) return;
    void navigate({ search: {}, replace: true });
    if (!dialogOpen) addTerm();
  }, [requestedNew, locked]);

  /**
   * Runs one single-term command per selected id inside one snapshot write, then
   * reports successes and failures with counts. Failed terms stay selected.
   */
  const runBulk = async (
    ids: number[],
    command: (term: Term) => Promise<Dictionary> | null,
    messages: { done: (count: number) => string; none: string },
  ) => {
    const result: { outcome?: BulkOutcome } = {};
    const saved = await write(async () => {
      result.outcome = await applyToTerms(ids, command);
      return result.outcome.snapshot;
    });
    const outcome = result.outcome;
    if (!saved || !outcome) return;
    const failed = outcome.failedIds.length;
    setSelection(new Set(outcome.failedIds));
    if (failed)
      toast.error(
        `${words(failed)} su ${ids.length} non ${plural(failed, "aggiornata", "aggiornate")}`,
        {
          description: `${outcome.done ? `${messages.done(outcome.done)}. ` : ""}${toUserMessage(outcome.error)}`,
        },
      );
    else if (outcome.done) toast.success(messages.done(outcome.done));
    else toast(messages.none);
  };
  const assignTo = (target: Group) =>
    void runBulk(
      [...selected],
      (item) =>
        item.groupIds.includes(target.id)
          ? null
          : dictionaryApi.saveTerm({
              ...item,
              groupIds: [...item.groupIds, target.id],
            }),
      {
        done: (count) =>
          `${words(count)} ${plural(count, "assegnata", "assegnate")} a “${target.name}”`,
        none: `Le parole selezionate sono già in “${target.name}”`,
      },
    );
  const removeFrom = (target: Group) =>
    void runBulk(
      [...selected],
      (item) =>
        item.groupIds.includes(target.id)
          ? dictionaryApi.saveTerm({
              ...item,
              groupIds: item.groupIds.filter((id) => id !== target.id),
            })
          : null,
      {
        done: (count) =>
          `${words(count)} ${plural(count, "rimossa", "rimosse")} da “${target.name}”`,
        none: `Nessuna parola selezionata è in “${target.name}”`,
      },
    );

  return (
    <Page>
      <PageHeader
        title="Dizionario"
        description="Grafie preferite e varianti da sostituire. Le modifiche valgono dalla prossima dettatura; la sessione attiva resta invariata."
        actions={
          <>
            {data && (
              <ActiveGroupsMenu
                groups={data.groups}
                selectedIds={data.selectedGroupIds}
                disabled={locked}
                onChange={(ids) =>
                  void write(() => dictionaryApi.selectGroups(ids))
                }
                onManage={() => setManageGroups(true)}
              />
            )}
            <Button
              ref={addButton}
              onClick={addTerm}
              disabled={locked}
              aria-keyshortcuts="Meta+N"
            >
              <Plus data-icon="inline-start" />
              Aggiungi parola
            </Button>
          </>
        }
      />
      <PageContent>
        {error && !dialogOpen && (
          <DictionaryError
            title={
              data
                ? "Dizionario non aggiornato"
                : "Impossibile aprire il dizionario"
            }
            error={error}
            onRetry={() => void refresh()}
          />
        )}
        {!data ? (
          loading && <TermTableSkeleton />
        ) : (
          <>
            {data.conflicts.length > 0 && (
              <Alert>
                <TriangleAlert />
                <AlertTitle>Varianti in conflitto</AlertTitle>
                <AlertDescription>
                  Varianti in conflitto tra {data.conflicts.length} termini.
                  Quando le regole hanno la stessa specificità, il testo
                  originale viene conservato. Modifica varianti, condizioni o
                  gruppi.
                </AlertDescription>
              </Alert>
            )}
            <DictionaryToolbar
              dictionary={data}
              counts={counts}
              search={search}
              filter={effectiveFilter}
              sort={sort}
              found={terms.length}
              locked={locked}
              searchRef={searchInput}
              groupsRef={groupsButton}
              onSearch={(value) => {
                setSearch(value);
                resetList();
              }}
              onFilter={(value) => {
                setFilter(value);
                resetList();
              }}
              onSort={(value) => {
                setSort(value);
                setPage(1);
              }}
              onManageGroups={() => {
                clearError();
                setManageGroups(true);
              }}
            />
            {selected.size > 0 && (
              <BulkActions
                count={selected.size}
                groups={data.groups}
                removable={data.groups.filter((item) =>
                  selectedTerms.some((term) => term.groupIds.includes(item.id)),
                )}
                busy={locked}
                onAssign={assignTo}
                onRemove={removeFrom}
                onDelete={() => {
                  rememberFocus();
                  setBulkDeletion([...selected]);
                }}
                onClear={() => setSelection(new Set())}
              />
            )}
            {visibleTerms.length ? (
              <TermTable
                terms={visibleTerms}
                groupNames={groupNames}
                conflicts={new Set(data.conflicts)}
                automatic={automatic}
                selection={selected}
                locked={locked}
                onSelect={(id, checked) =>
                  setSelection((current) => {
                    const next = new Set(current);
                    if (checked) next.add(id);
                    else next.delete(id);
                    return next;
                  })
                }
                onSelectPage={(checked) =>
                  setSelection((current) => {
                    const next = new Set(current);
                    for (const item of visibleTerms)
                      if (checked) next.add(item.id);
                      else next.delete(item.id);
                    return next;
                  })
                }
                onOpen={(item, element) =>
                  openEditor({ initial: item }, element)
                }
                onDuplicate={(item, element) =>
                  openEditor(
                    {
                      initial: { ...item, id: null },
                      title: "Duplica parola",
                    },
                    element,
                  )
                }
                onDelete={(item, element) =>
                  openDelete(
                    { kind: "term", id: item.id, label: item.text },
                    element,
                  )
                }
              />
            ) : (
              <Empty className="border">
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <BookOpen />
                  </EmptyMedia>
                  <EmptyTitle>
                    {!data.terms.length
                      ? "Il tuo dizionario inizia qui"
                      : search.trim()
                        ? "Nessuna parola trovata"
                        : "Nessuna parola in questo filtro"}
                  </EmptyTitle>
                  <EmptyDescription>
                    {!data.terms.length
                      ? "Aggiungi nomi e termini da scrivere sempre nello stesso modo. Puoi raccoglierli in gruppi."
                      : "Prova un'altra ricerca o cambia il gruppo selezionato."}
                  </EmptyDescription>
                </EmptyHeader>
                <EmptyContent>
                  {!data.terms.length ? (
                    <Button onClick={addTerm} disabled={locked}>
                      <Plus data-icon="inline-start" />
                      Aggiungi parola
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      onClick={() => {
                        setSearch("");
                        setFilter("all");
                        resetList();
                      }}
                    >
                      Mostra tutte le parole
                    </Button>
                  )}
                </EmptyContent>
              </Empty>
            )}
            {terms.length > 0 && (
              <TermPagination
                page={currentPage}
                pageCount={pageCount}
                pageSize={PAGE_SIZE}
                total={terms.length}
                onPage={setPage}
              />
            )}
            <AutoVariants variants={autoVariants} disabled={busy} />
          </>
        )}
      </PageContent>
      {manageGroups && data && (
        <GroupManager
          dictionary={data}
          counts={counts}
          locked={locked}
          childOpen={!!group || !!deletion}
          returnFocus={groupsButton}
          onClose={() => setManageGroups(false)}
          onEdit={openGroup}
          onDelete={(target) => openDelete(target)}
        />
      )}
      {editor && data && (
        <TermDialog
          initial={editor.initial}
          title={editor.title}
          groups={data.groups}
          busy={busy}
          error={error}
          onClose={closeDialog}
          onReturnFocus={returnFocus}
          onSave={(input) => {
            void mutate(() => dictionaryApi.saveTerm(input)).then((saved) => {
              if (!saved) return;
              toast.success(
                input.id === null ? "Parola aggiunta" : "Parola salvata",
              );
              closeDialog();
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
              if (!saved) return;
              toast.success(
                input.id === null ? "Gruppo creato" : "Gruppo rinominato",
              );
              closeDialog();
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
              if (!saved) return;
              toast.success(
                deletion.kind === "term"
                  ? "Parola eliminata"
                  : "Gruppo eliminato. Le parole sono conservate.",
              );
              closeDialog();
            });
          }}
        />
      )}
      {bulkDeletion && (
        <BulkDeleteDialog
          count={bulkDeletion.length}
          busy={busy}
          onClose={() => setBulkDeletion(null)}
          onReturnFocus={() =>
            (opener.current?.isConnected
              ? opener.current
              : searchInput.current
            )?.focus()
          }
          onConfirm={() => {
            void runBulk(
              bulkDeletion,
              (item) => dictionaryApi.deleteTerm(item.id),
              {
                done: (count) =>
                  `${words(count)} ${plural(count, "eliminata", "eliminate")}`,
                none: "Le parole selezionate erano già state eliminate",
              },
            ).then(() => setBulkDeletion(null));
          }}
        />
      )}
    </Page>
  );
}
