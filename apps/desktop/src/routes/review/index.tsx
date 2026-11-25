import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { z } from "zod";
import { ArrowLeftIcon, LightbulbIcon } from "lucide-react";
import { toast } from "sonner";
import { useSnapshotQuery } from "@/hooks/use-snapshot-query";
import { useShortcut } from "@/hooks/use-shortcut";
import { queryKeys } from "@/lib/query-client";
import { optionalIdSearch } from "@/lib/route-params";
import { toUserMessage } from "@/lib/errors";
import { cn } from "@/lib/utils";
import {
  filterFor,
  matchesFilter,
  suggestionsApi,
  type Proposal,
  type ReviewAction,
  type ReviewFilter,
  type ReviewRequest,
} from "@/lib/suggestions";
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Kbd } from "@/components/ui/kbd";
import { Skeleton } from "@/components/ui/skeleton";
import { ProposalQueue } from "@/components/suggestions/proposal-queue";
import {
  availableCommands,
  ProposalDetail,
  type ProposalCommand,
} from "@/components/suggestions/proposal-detail";
import {
  ReviewDialog,
  type ReviewDialogState,
} from "@/components/suggestions/review-dialog";
import { Publications } from "@/components/suggestions/publications";

export const Route = createFileRoute("/review/")({
  validateSearch: z.object({
    proposal: optionalIdSearch,
    publication: optionalIdSearch,
  }),
  component: SuggestionsPage,
});

const publishing = new Set<ReviewAction["kind"]>([
  "approve",
  "disable",
  "restore",
]);
// Actions that take the proposal out of the triage flow move to the next one.
const advancing = new Set<ReviewAction["kind"]>([
  "approve",
  "defer",
  "reject",
  "disable",
]);

// Letter shortcuts stay off while a dialog, menu or list box owns the keyboard.
const overlayOpen = () =>
  document.querySelector(
    '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]',
  ) !== null;
// Matches the `lg` breakpoint that shows list and detail side by side.
const sideBySide = () => window.matchMedia("(min-width: 64rem)").matches;

function SuggestionsPage() {
  const { proposal: linkedProposal, publication: linkedPublication } =
    Route.useSearch();
  const navigate = Route.useNavigate();
  const {
    data,
    busy: writing,
    loading,
    error,
    clearError,
    refresh,
    mutate,
  } = useSnapshotQuery(
    queryKeys.suggestions,
    suggestionsApi.read,
    [queryKeys.dictionary, queryKeys.analysis],
    0,
  ); // The background worker can ingest proposals while this route is closed.
  const busy = writing || loading;
  // Local and read-only. A new publication or a history change asks again.
  const replay = useQuery({
    queryKey: [...queryKeys.suggestions, "replay", data?.version ?? null],
    queryFn: suggestionsApi.replay,
    enabled: data !== null,
    staleTime: 60_000,
  });
  const replayOf = (id: number) =>
    replay.data?.rules.find((rule) => rule.proposalId === id) ?? null;
  const [filter, setFilter] = useState<ReviewFilter>("review");
  const [dialog, setDialog] = useState<ReviewDialogState | null>(null);
  const retry = useRef<{ identity: string; request: ReviewRequest } | null>(
    null,
  );
  const opener = useRef<HTMLElement | null>(null);

  const proposals = data?.proposals ?? [];
  const queue = proposals.filter((p) => matchesFilter(p, filter));
  const linked =
    linkedProposal === undefined
      ? null
      : (proposals.find((p) => p.id === linkedProposal) ?? null);
  // Without a link the first proposal of the queue is shown on wide layouts.
  const selected = linked ?? (linkedProposal === undefined ? queue[0] : null);
  const index = selected ? queue.findIndex((p) => p.id === selected.id) : -1;

  // A deep link opens the queue that contains the linked proposal, once.
  const synced = useRef<number | null>(null);
  useEffect(() => {
    if (!linked || synced.current === linked.id) return;
    synced.current = linked.id;
    if (!matchesFilter(linked, filter)) setFilter(filterFor(linked));
  }, [linked, filter]);
  // Keyboard and post-action moves keep the explicit selection in view.
  useEffect(() => {
    if (linkedProposal !== undefined)
      document
        .querySelector(`[data-proposal-id="${linkedProposal}"]`)
        ?.scrollIntoView({ block: "nearest" });
  }, [linkedProposal]);

  const select = (id: number | undefined) =>
    void navigate({
      search: (prev) => ({ ...prev, proposal: id }),
      replace: true,
    });
  const step = (offset: 1 | -1) => {
    if (!queue.length) return;
    const next = index < 0 ? 0 : index + offset;
    if (next >= 0 && next < queue.length) select(queue[next].id);
  };

  async function act(proposal: Proposal | null, action: ReviewAction) {
    if (!data || busy) return;
    const identity = JSON.stringify({
      id: proposal?.id,
      revision: proposal?.revision,
      version: data.version,
      dictionary: data.dictionaryVersion,
      action,
    });
    const request =
      retry.current?.identity === identity
        ? retry.current.request
        : {
            id: proposal?.id ?? null,
            expectedRevision: proposal?.revision ?? null,
            expectedVersion: data.version,
            dictionaryVersion: data.dictionaryVersion,
            requestKey: crypto.randomUUID(),
            action,
          };
    retry.current = { identity, request };
    // The neighbour is taken before the write: the snapshot may drop the item.
    const at = proposal ? queue.findIndex((p) => p.id === proposal.id) : -1;
    const neighbour = at < 0 ? null : (queue[at + 1] ?? queue[at - 1] ?? null);
    clearError();
    let failure: unknown = null;
    const next = await mutate(() =>
      suggestionsApi.review(request).catch((error: unknown) => {
        failure = error;
        throw error;
      }),
    );
    if (next) {
      retry.current = null;
      setDialog(null);
      if (publishing.has(action.kind))
        toast.success(`Versione ${next.version} pubblicata`, {
          description: "Disponibile dalla prossima sessione.",
        });
      else
        toast.success(
          action.kind === "defer"
            ? "Proposta rimandata"
            : action.kind === "reject"
              ? "Proposta rifiutata"
              : "Proposta salvata",
          { description: "Nessuna nuova regola attivata." },
        );
      if (proposal && at >= 0 && advancing.has(action.kind))
        select(neighbour?.id);
    } else if (failure !== null && !dialog) {
      // Without a dialog the error has nowhere to stay: show it once.
      clearError();
      toast.error(toUserMessage(failure));
    }
  }
  function open(next: ReviewDialogState) {
    opener.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    clearError();
    setDialog(next);
  }
  function command(proposal: Proposal, name: ProposalCommand) {
    if (name === "defer" || name === "disable")
      void act(proposal, { kind: name });
    else open({ kind: name, proposal });
  }
  function closeDialog() {
    clearError();
    setDialog(null);
  }
  function returnFocus() {
    const target = opener.current?.isConnected
      ? opener.current
      : document.querySelector<HTMLElement>(
          '[aria-label="Azioni sulla proposta"] button:not(:disabled), [data-proposal-id][aria-current="page"]',
        );
    target?.focus();
  }

  // Triage shortcuts, ignored while typing and while an overlay is open.
  const shortcuts = { enabled: !!data && !dialog };
  // On narrow layouts the implicit selection is not visible: act on links only.
  const visible = () => linkedProposal !== undefined || sideBySide();
  const run = (name: ProposalCommand) => () => {
    if (!selected || busy || overlayOpen() || !visible()) return;
    const can = availableCommands(selected);
    if (can[name]) command(selected, name);
  };
  const move = (offset: 1 | -1) => () => {
    if (overlayOpen()) return;
    if (visible()) step(offset);
    else if (queue.length) select(queue[0].id);
  };
  useShortcut("a", run("approve"), shortcuts);
  useShortcut("e", run("edit"), shortcuts);
  useShortcut("r", run("defer"), shortcuts);
  useShortcut("x", run("reject"), shortcuts);
  useShortcut("j", move(1), shortcuts);
  useShortcut("arrowdown", move(1), shortcuts);
  useShortcut("k", move(-1), shortcuts);
  useShortcut("arrowup", move(-1), shortcuts);

  const loadFailed = error !== null && !dialog;
  if (!data)
    return loadFailed ? (
      <LoadError message={error} onRetry={() => void refresh()} />
    ) : (
      <div className="grid gap-6 lg:grid-cols-5">
        <div className="flex flex-col gap-2 lg:col-span-2">
          <Skeleton className="h-7 w-40" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
        <div className="hidden flex-col gap-3 lg:col-span-3 lg:flex">
          <Skeleton className="h-6 w-1/2" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      </div>
    );

  // Narrow layouts show the list, or the linked proposal with a way back.
  const detailOnly = linkedProposal !== undefined;
  return (
    <>
      {loadFailed && (
        <LoadError message={error} onRetry={() => void refresh()} />
      )}
      <div className="grid items-start gap-6 lg:grid-cols-5">
        <div className={cn("lg:col-span-2", detailOnly && "max-lg:hidden")}>
          <ProposalQueue
            proposals={queue}
            selectedId={selected?.id ?? null}
            filter={filter}
            onFilterChange={setFilter}
            publication={linkedPublication}
          >
            {queue.length > 0 && (
              <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground max-lg:hidden">
                <span className="inline-flex items-center gap-1">
                  <Kbd>J</Kbd>
                  <Kbd>K</Kbd>
                  per scorrere
                </span>
                <span className="inline-flex items-center gap-1">
                  <Kbd>A</Kbd>
                  <Kbd>E</Kbd>
                  <Kbd>R</Kbd>
                  <Kbd>X</Kbd>
                  per decidere
                </span>
              </p>
            )}
            {queue.length === 0 && (
              <QueueEmpty filter={filter} onShowAll={() => setFilter("all")} />
            )}
          </ProposalQueue>
        </div>
        <div
          className={cn(
            "flex min-w-0 flex-col gap-4 lg:col-span-3 lg:border-l lg:pl-6",
            !detailOnly && "max-lg:hidden",
          )}
        >
          {detailOnly && (
            <Button
              variant="ghost"
              size="sm"
              className="self-start lg:hidden"
              onClick={() => select(undefined)}
            >
              <ArrowLeftIcon data-icon="inline-start" />
              Torna alla coda
            </Button>
          )}
          {linkedProposal !== undefined && !linked && (
            <Alert>
              <AlertDescription>
                Proposta #{linkedProposal} non più disponibile.
              </AlertDescription>
              <AlertAction>
                <Button asChild variant="outline" size="xs">
                  <Link to="/review" search={{}}>
                    Mostra la coda
                  </Link>
                </Button>
              </AlertAction>
            </Alert>
          )}
          {selected ? (
            <ProposalDetail
              key={selected.id}
              proposal={selected}
              groupNames={new Map(data.groups.map((g) => [g.id, g.name]))}
              replay={replayOf(selected.id)}
              replaySessions={replay.data?.sessions ?? 0}
              replayPending={replay.isPending}
              busy={busy}
              position={index >= 0 ? { index, total: queue.length } : null}
              onCommand={(name) => command(selected, name)}
              onStep={step}
            />
          ) : (
            linked === null &&
            linkedProposal === undefined && (
              <p className="text-sm text-muted-foreground">
                Seleziona una proposta dalla coda.
              </p>
            )
          )}
        </div>
      </div>
      <Publications
        snapshot={data}
        linked={linkedPublication}
        busy={busy}
        onRestore={(version) => open({ kind: "restore", version })}
      />
      {dialog && (
        <ReviewDialog
          key={
            dialog.kind === "restore"
              ? `restore:${dialog.version}`
              : `${dialog.kind}:${dialog.proposal.id}`
          }
          dialog={dialog}
          groups={data.groups}
          replay={
            dialog.kind === "approve" ? replayOf(dialog.proposal.id) : null
          }
          replaySessions={replay.data?.sessions ?? 0}
          busy={busy}
          error={error}
          onClose={closeDialog}
          onConfirm={(proposal, action) => void act(proposal, action)}
          returnFocus={returnFocus}
        />
      )}
    </>
  );
}

function LoadError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <Alert variant="destructive">
      <AlertDescription>{message}</AlertDescription>
      <AlertAction>
        <Button variant="outline" size="xs" onClick={onRetry}>
          Riprova
        </Button>
      </AlertAction>
    </Alert>
  );
}

function QueueEmpty({
  filter,
  onShowAll,
}: {
  filter: ReviewFilter;
  onShowAll: () => void;
}) {
  return (
    <Empty className="border">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <LightbulbIcon />
        </EmptyMedia>
        <EmptyTitle>
          {filter === "review"
            ? "Nessuna proposta da revisionare"
            : "Nessuna proposta in questa vista"}
        </EmptyTitle>
        <EmptyDescription>
          {filter === "review"
            ? "Le proposte compaiono dopo un'analisi autorizzata. Le correzioni dello storico restano esempi."
            : "Cambia filtro per vedere le altre proposte."}
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        {filter === "review" ? (
          <Button asChild variant="outline" size="sm">
            <Link to="/review/runs">Vai alle esecuzioni</Link>
          </Button>
        ) : (
          <Button variant="outline" size="sm" onClick={onShowAll}>
            Mostra tutte
          </Button>
        )}
      </EmptyContent>
    </Empty>
  );
}
