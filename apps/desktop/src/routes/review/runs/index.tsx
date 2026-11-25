import { useCallback } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import {
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  HistoryIcon,
} from "lucide-react";
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  Pagination,
  PaginationContent,
  PaginationItem,
} from "@/components/ui/pagination";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  analysisSearch,
  RunLink,
  runCount,
  runDate,
  runDuration,
  runOrigin,
  useOpenRun,
} from "@/components/analysis/run-navigation";
import { useRunResource } from "@/components/analysis/use-run-resource";
import { analysisApi } from "@/lib/analysis";
import {
  invocationReasons,
  runsApi,
  runStates,
  runStateVariant,
  type RunKey,
} from "@/lib/analysis-runs";
import { schedulerApi } from "@/lib/scheduler";

export const Route = createFileRoute("/review/runs/")({
  validateSearch: analysisSearch,
  component: AnalysisPage,
});
const listInterval = (data: Awaited<ReturnType<typeof loadStatus>>) =>
  data.status.scheduler.workerRunning ||
  data.page.entries.some((r) => r.state === "running") ||
  data.invocations.some(
    (j) => j.reason === "requested" || j.reason === "running",
  )
    ? 3000
    : null;
async function loadStatus(
  before: RunKey | null,
  state?: string,
  origin?: string,
) {
  const [page, analysis, scheduler, invocations] = await Promise.all([
    runsApi.page(before, { state, origin }),
    analysisApi.status(),
    schedulerApi.status(),
    runsApi.invocations(),
  ]);
  return { page, status: { analysis, scheduler }, invocations };
}
function AnalysisPage() {
  const search = Route.useSearch();
  // Key the data view by its query, so previous filters never flash in a new URL.
  return (
    <AnalysisList
      key={`${search.state}:${search.origin}:${search.beforeBatch}:${search.beforeAttempt}`}
    />
  );
}
function AnalysisList() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const openRun = useOpenRun();
  const { beforeBatch, beforeAttempt, state, origin } = search;
  const load = useCallback(
    () =>
      loadStatus(
        beforeBatch
          ? { batchId: beforeBatch, attemptId: beforeAttempt ?? null }
          : null,
        state,
        origin,
      ),
    [beforeBatch, beforeAttempt, state, origin],
  );
  // Polled while work is running; focus and history events refresh it too.
  const { data, busy, error, refresh } = useRunResource(
    ["analysis", "runs", beforeBatch, beforeAttempt, state, origin],
    load,
    listInterval,
  );
  const filter = (name: "state" | "origin", value: string) =>
    void navigate({
      search: {
        ...search,
        [name]: value === "all" ? undefined : value,
        beforeBatch: undefined,
        beforeAttempt: undefined,
        selectedBatch: undefined,
        selectedAttempt: undefined,
      },
    });
  const filtered = !!(state || origin);
  return (
    <>
      <div
        role="toolbar"
        aria-label="Filtri delle esecuzioni"
        className="flex flex-wrap items-center gap-2"
      >
        <Select
          value={state ?? "all"}
          onValueChange={(v) => filter("state", v)}
        >
          <SelectTrigger
            size="sm"
            className="min-w-44"
            aria-label="Filtra per esito"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="all">Tutti gli esiti</SelectItem>
              {Object.entries(runStates).map(([v, label]) => (
                <SelectItem value={v} key={v}>
                  {label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
        <Select
          value={origin ?? "all"}
          onValueChange={(v) => filter("origin", v)}
        >
          <SelectTrigger
            size="sm"
            className="min-w-40"
            aria-label="Filtra per origine"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="all">Tutte le origini</SelectItem>
              <SelectItem value="manual">Manuale</SelectItem>
              <SelectItem value="automatic">Programmata</SelectItem>
              <SelectItem value="unknown">Non disponibile</SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
        {filtered && (
          <Button asChild variant="ghost" size="sm">
            <Link to="/review/runs" search={{}}>
              Rimuovi filtri
            </Link>
          </Button>
        )}
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>
            Impossibile caricare le esecuzioni.
          </AlertDescription>
          <AlertAction>
            <Button
              variant="outline"
              size="xs"
              disabled={busy}
              onClick={refresh}
            >
              Riprova
            </Button>
          </AlertAction>
        </Alert>
      )}
      <section
        aria-label="Tentativi di analisi"
        className="flex min-w-0 flex-col gap-3"
      >
        {!data && busy && (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        )}
        {data?.page.entries.length === 0 && (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <HistoryIcon />
              </EmptyMedia>
              <EmptyTitle>
                {filtered || beforeBatch
                  ? "Nessuna analisi in questa vista"
                  : "Nessuna analisi registrata"}
              </EmptyTitle>
              <EmptyDescription>
                {filtered
                  ? "Nessuna esecuzione corrisponde ai filtri."
                  : "Le esecuzioni compaiono qui dopo il primo avvio."}
              </EmptyDescription>
            </EmptyHeader>
            {filtered && (
              <EmptyContent>
                <Button asChild variant="outline" size="sm">
                  <Link to="/review/runs" search={{}}>
                    Rimuovi filtri
                  </Link>
                </Button>
              </EmptyContent>
            )}
          </Empty>
        )}
        {!!data?.page.entries.length && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Data</TableHead>
                <TableHead>Origine</TableHead>
                <TableHead>Esito</TableHead>
                <TableHead className="text-right">Fonti</TableHead>
                <TableHead>Risultati</TableHead>
                <TableHead className="text-right">Durata</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.page.entries.map((run) => (
                <TableRow
                  key={`${run.key.batchId}:${run.key.attemptId}`}
                  className="cursor-pointer"
                  data-state={
                    search.selectedBatch === run.key.batchId &&
                    (search.selectedAttempt ?? null) === run.key.attemptId
                      ? "selected"
                      : undefined
                  }
                  onClick={(event) => {
                    // The date link handles its own clicks and modifiers.
                    if ((event.target as Element).closest("a")) return;
                    void openRun(run.key, search);
                  }}
                >
                  <TableCell>
                    <RunLink
                      runKey={run.key}
                      search={search}
                      className="flex flex-col font-medium hover:underline"
                    >
                      {runDate(run.startedAt ?? run.createdAt)}
                      <span className="text-xs font-normal text-muted-foreground">
                        Lotto #{run.key.batchId} ·{" "}
                        {run.key.attemptId === null
                          ? "Preparato"
                          : `Tentativo #${run.key.attemptId}`}
                      </span>
                    </RunLink>
                  </TableCell>
                  <TableCell>{runOrigin(run.origin)}</TableCell>
                  <TableCell>
                    <Badge variant={runStateVariant(run.state)}>
                      {runStates[run.state] ?? run.state}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {runCount(run.sourceCount)}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {run.accepted === null && run.rejected === null
                      ? "Non disponibile"
                      : `${runCount(run.accepted)} accettati · ${runCount(run.rejected)} scartati`}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {runDuration(run.elapsedMs)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {(beforeBatch || data?.page.nextCursor) && (
          <Pagination aria-label="Pagine delle esecuzioni">
            <PaginationContent>
              {beforeBatch && (
                <PaginationItem>
                  <Button asChild variant="ghost">
                    <Link
                      to="/review/runs"
                      search={{
                        ...search,
                        beforeBatch: undefined,
                        beforeAttempt: undefined,
                      }}
                    >
                      <ChevronLeftIcon data-icon="inline-start" />
                      Più recenti
                    </Link>
                  </Button>
                </PaginationItem>
              )}
              {data?.page.nextCursor && (
                <PaginationItem>
                  <Button asChild variant="ghost">
                    <Link
                      to="/review/runs"
                      search={{
                        ...search,
                        beforeBatch: data.page.nextCursor.batchId,
                        beforeAttempt:
                          data.page.nextCursor.attemptId ?? undefined,
                      }}
                    >
                      Precedenti
                      <ChevronRightIcon data-icon="inline-end" />
                    </Link>
                  </Button>
                </PaginationItem>
              )}
            </PaginationContent>
          </Pagination>
        )}
      </section>
      <Collapsible className="flex flex-col gap-3">
        <CollapsibleTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            className="group/trigger self-start"
          >
            Diagnostica degli avvii
            <ChevronDownIcon
              data-icon="inline-end"
              className="transition-transform group-data-[state=open]/trigger:rotate-180"
            />
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="flex flex-col gap-3 text-sm">
          <p className="text-muted-foreground">
            Ultimi 100 avvii locali, inclusi quelli senza fonti. Gli avvii
            automatici consecutivi saltati per lo stesso motivo occupano una
            sola riga. Un avvio non certifica un'analisi riuscita.
          </p>
          {data?.invocations.length === 0 && (
            <p className="text-muted-foreground">Nessun avvio registrato.</p>
          )}
          {!!data?.invocations.length && (
            <ul className="flex flex-col divide-y">
              {data.invocations.map((job) => (
                <li
                  className="flex flex-wrap items-center gap-2 py-2"
                  key={job.id}
                >
                  <time className="tabular-nums">{runDate(job.startedAt)}</time>
                  {job.endedAt !== null &&
                    job.endedAt - job.startedAt > 60_000 && (
                      <span className="text-muted-foreground">
                        fino a {runDate(job.endedAt)}
                      </span>
                    )}
                  <Badge variant="outline">{runOrigin(job.origin)}</Badge>
                  <span>{invocationReasons[job.reason] ?? job.reason}</span>
                  {job.batchId !== null && job.attemptId !== null && (
                    <Button variant="link" size="sm" asChild>
                      <RunLink
                        runKey={{
                          batchId: job.batchId,
                          attemptId: job.attemptId,
                        }}
                        search={search}
                      >
                        Apri tentativo #{job.attemptId}
                      </RunLink>
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CollapsibleContent>
      </Collapsible>
    </>
  );
}
