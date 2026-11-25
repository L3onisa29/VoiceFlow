import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { ChevronDownIcon } from "lucide-react";
import { PageSection, PageSectionHeader } from "@/components/layout/page";
import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Item,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemTitle,
} from "@/components/ui/item";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfidenceBadge } from "@/components/suggestions/confidence-badge";
import { TextComparison } from "@/components/suggestions/text-comparison";
import {
  rejectionCodes,
  runsApi,
  runStates,
  runStateVariant,
  spellingOutcomes,
  type RunDetail,
  type RunKey,
} from "@/lib/analysis-runs";
import {
  confidenceMeaning,
  findingActions,
  findingOutcomes,
} from "@/lib/confidence";
import { toUserMessage } from "@/lib/errors";
import { stateLabels } from "@/lib/suggestions";
import { RunSource } from "./run-source";
import {
  RunLink,
  runCount,
  runDate,
  runDuration,
  runOrigin,
  type AnalysisSearch,
} from "./run-navigation";
import { useRunResource } from "./use-run-resource";

const detailInterval = (data: RunDetail) =>
  data.run.state === "running" ? 3000 : 30000;

const outcomeVariant = (outcome: string) =>
  outcome === "auto-approved"
    ? ("success" as const)
    : outcome.startsWith("blocked-")
      ? ("warning" as const)
      : ("outline" as const);

function RunBreadcrumb({
  search,
  batchId,
}: {
  search: AnalysisSearch;
  batchId: number | null;
}) {
  return (
    <Breadcrumb aria-label="Percorso">
      <BreadcrumbList>
        <BreadcrumbItem>
          <BreadcrumbLink asChild>
            <Link to="/review">Revisione</Link>
          </BreadcrumbLink>
        </BreadcrumbItem>
        <BreadcrumbSeparator />
        <BreadcrumbItem>
          <BreadcrumbLink asChild>
            <Link to="/review/runs" search={search}>
              Esecuzioni
            </Link>
          </BreadcrumbLink>
        </BreadcrumbItem>
        <BreadcrumbSeparator />
        <BreadcrumbItem>
          <BreadcrumbPage>
            {batchId === null ? "Collegamento non valido" : `Lotto #${batchId}`}
          </BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  );
}

export function InvalidRun({ search }: { search: AnalysisSearch }) {
  return (
    <>
      <RunBreadcrumb search={search} batchId={null} />
      <PageSectionHeader title="Analisi non disponibile" />
      <Alert variant="destructive">
        <AlertDescription>
          Il collegamento contiene un identificatore non valido.
        </AlertDescription>
        <AlertAction>
          <Button asChild variant="outline" size="xs">
            <Link to="/review/runs" search={search}>
              Torna alle esecuzioni
            </Link>
          </Button>
        </AlertAction>
      </Alert>
    </>
  );
}

export function RunDetailPage({
  runKey,
  search,
}: {
  runKey: RunKey;
  search: AnalysisSearch;
}) {
  const { batchId, attemptId } = runKey;
  const load = useCallback(
    () => runsApi.detail({ batchId, attemptId }),
    [batchId, attemptId],
  );
  // Polled (3 s while running, 30 s otherwise) and refreshed on focus.
  const { data, error, refresh } = useRunResource(
    ["analysis", "detail", batchId, attemptId],
    load,
    detailInterval,
  );
  const [source, setSource] = useState<{
    sessionId: number;
    revisionId: number | null;
  } | null>(null);
  // Source inspection is read-only; privacy/error refresh can safely unmount it.
  const visibleSource =
    data &&
    source &&
    data.sources.some(
      (s) =>
        s.sessionId === source.sessionId &&
        s.revisionId === source.revisionId &&
        !s.excluded,
    )
      ? source
      : null;
  useEffect(() => {
    if (
      source &&
      data &&
      !data.sources.some(
        (s) =>
          s.sessionId === source.sessionId &&
          s.revisionId === source.revisionId &&
          !s.excluded,
      )
    )
      setSource(null);
    if (error) setSource(null);
  }, [source, data, error]);
  const backSearch = {
    ...search,
    selectedBatch: batchId,
    selectedAttempt: attemptId ?? undefined,
  };
  return (
    <section
      aria-label="Dettaglio analisi"
      className="flex min-w-0 flex-col gap-6"
    >
      <div className="flex flex-col gap-3">
        <RunBreadcrumb search={backSearch} batchId={batchId} />
        <PageSectionHeader
          title={
            attemptId === null ? "Lotto preparato" : `Tentativo #${attemptId}`
          }
          actions={
            data && (
              <Badge variant={runStateVariant(data.run.state)}>
                {runStates[data.run.state] ?? data.run.state}
              </Badge>
            )
          }
        />
        {data && (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-5">
            <Fact label="Data">
              {runDate(data.run.startedAt ?? data.run.createdAt)}
            </Fact>
            <Fact label="Durata">{runDuration(data.run.elapsedMs)}</Fact>
            <Fact label="Origine">{runOrigin(data.run.origin)}</Fact>
            <Fact label="Fonti">{runCount(data.run.sourceCount)}</Fact>
            <Fact label="Risultati">
              {data.run.accepted === null
                ? "Non disponibile"
                : `${data.run.accepted} accettati · ${runCount(data.run.rejected)} scartati`}
            </Fact>
          </dl>
        )}
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Dettaglio non disponibile</AlertTitle>
          <AlertDescription>
            {error} L'analisi potrebbe essere stata eliminata.
          </AlertDescription>
          <AlertAction>
            <Button variant="outline" size="xs" onClick={refresh}>
              Riprova dettaglio
            </Button>
          </AlertAction>
        </Alert>
      )}
      {!data && !error && (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      )}
      {data && (
        <>
          {(attemptId === null ||
            (data.run.state === "completed" && data.run.accepted === 0) ||
            data.run.sourceCount === null) && (
            <div className="flex flex-col gap-1 text-sm text-muted-foreground">
              {attemptId === null && (
                <p>
                  Fonti preparate. Nessuna richiesta inviata per questo lotto.
                </p>
              )}
              {data.run.state === "completed" && data.run.accepted === 0 && (
                <p>Analisi completata senza suggerimenti.</p>
              )}
              {data.run.sourceCount === null && (
                <p>
                  Record precedente allo storico dettagliato. Conteggi e
                  configurazione mancanti non sono disponibili.
                </p>
              )}
            </div>
          )}
          {data.run.error && (
            <Alert>
              <AlertDescription>
                <p>{toUserMessage(data.run.error)}</p>
                {["failed", "interrupted", "cancelled"].includes(
                  data.run.state,
                ) && (
                  <p>
                    Le fonti valide restano riprendibili con un nuovo avvio, nel
                    rispetto dei limiti.
                  </p>
                )}
              </AlertDescription>
            </Alert>
          )}
          {data.attempts.length > 0 && (
            <nav
              aria-label="Tentativi dello stesso lotto"
              className="flex flex-wrap items-center gap-2"
            >
              <span className="text-sm text-muted-foreground">
                Tentativi del lotto
              </span>
              {data.attempts.map((attempt) => (
                <Button
                  key={attempt.key.attemptId}
                  asChild
                  variant={
                    attempt.key.attemptId === attemptId
                      ? "secondary"
                      : "outline"
                  }
                  size="xs"
                >
                  <RunLink
                    runKey={attempt.key}
                    search={search}
                    aria-current={
                      attempt.key.attemptId === attemptId ? "page" : undefined
                    }
                  >
                    #{attempt.key.attemptId} ·{" "}
                    {runStates[attempt.state] ?? attempt.state}
                  </RunLink>
                </Button>
              ))}
            </nav>
          )}
          <PageSection
            title="Rilievi e decisioni"
            description={`Esiti di questo tentativo: lo stato attuale della proposta può essere cambiato. ${confidenceMeaning}`}
          >
            {data.findings.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Nessun rilievo disponibile. Esclusioni ed eliminazioni delle
                fonti rimuovono anche i rilievi collegati.
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                {data.findings.map((finding) => (
                  <Item key={finding.id} variant="outline" asChild>
                    <article aria-label={`Rilievo: ${finding.name}`}>
                      <ItemContent className="min-w-0 gap-3">
                        <div className="flex flex-col gap-1">
                          <ItemTitle>{finding.name}</ItemTitle>
                          <ItemDescription className="line-clamp-none">
                            {finding.reason}
                          </ItemDescription>
                        </div>
                        <TextComparison
                          before={finding.text}
                          after={finding.resultText}
                          afterLabel="Risultato consigliato"
                        />
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Badge variant="outline">
                            {findingActions[finding.action]}
                          </Badge>
                          <ConfidenceBadge score={finding.confidence} />
                          <Badge
                            variant={outcomeVariant(finding.outcome)}
                            className="h-auto whitespace-normal"
                          >
                            {findingOutcomes[finding.outcome] ??
                              finding.outcome}
                          </Badge>
                        </div>
                      </ItemContent>
                      <ItemFooter className="flex-wrap">
                        <span className="text-xs text-muted-foreground">
                          {finding.evidence
                            .map(
                              (e) =>
                                `${e.revisionId === null ? "Trascrizione" : "Correzione"} #${e.sessionId}${e.revisionId === null ? "" : `, revisione #${e.revisionId}`}${e.originalIndex === null ? "" : `, segmento ${e.originalIndex + 1}`}`,
                            )
                            .join(" · ")}
                        </span>
                        <span className="flex flex-wrap gap-2">
                          {finding.proposalId !== null && (
                            <Button asChild variant="outline" size="xs">
                              <Link
                                to="/review"
                                search={{ proposal: finding.proposalId }}
                              >
                                Apri proposta #{finding.proposalId}
                              </Link>
                            </Button>
                          )}
                          {finding.publication !== null && (
                            <Button asChild variant="ghost" size="xs">
                              <Link
                                to="/review"
                                search={{ publication: finding.publication }}
                              >
                                Pubblicazione storica #{finding.publication}
                              </Link>
                            </Button>
                          )}
                        </span>
                      </ItemFooter>
                    </article>
                  </Item>
                ))}
              </div>
            )}
          </PageSection>
          {data.spellings.length > 0 && (
            <PageSection
              title="Grafie osservate"
              description="Parole storpiate segnalate in questo tentativo. Si sommano ai lotti precedenti: una grafia entra nel dizionario solo quando tutte le sessioni analizzate concordano."
            >
              <ul className="flex flex-col divide-y text-sm">
                {data.spellings.map((spelling) => (
                  <li
                    key={`${spelling.spelling}:${spelling.intended}`}
                    className="flex flex-wrap items-center gap-2 py-2"
                  >
                    <span className="font-medium">
                      «{spelling.spelling}» → {spelling.intended}
                    </span>
                    <Badge
                      variant={
                        spelling.outcome === "added" ? "success" : "outline"
                      }
                      className="h-auto whitespace-normal"
                    >
                      {spellingOutcomes[spelling.outcome] ?? spelling.outcome}
                    </Badge>
                    <span className="text-muted-foreground">
                      {spelling.sessionIds.length === 1
                        ? "1 sessione"
                        : `${spelling.sessionIds.length} sessioni`}{" "}
                      · confidenza {spelling.confidence}/100
                      {spelling.realWord ? " · parola esistente" : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </PageSection>
          )}
          {data.rejections.length > 0 && (
            <PageSection
              title="Candidati scartati"
              description="Motivo registrato dal controllo locale per ogni proposta scartata. Il testo proposto non viene conservato."
            >
              <ul className="flex flex-col divide-y text-sm">
                {data.rejections.map((rejection) => (
                  <li
                    key={`${rejection.kind}:${rejection.position}`}
                    className="flex flex-wrap items-center gap-2 py-2"
                  >
                    <Badge variant="outline">
                      {rejection.kind === "misspelling"
                        ? "Grafia"
                        : "Candidato"}{" "}
                      {rejection.position}
                    </Badge>
                    <span>
                      {rejectionCodes[rejection.code] ?? rejection.code}
                    </span>
                    {rejection.sources.length > 0 && (
                      <span className="text-muted-foreground">
                        {rejection.sources
                          .map(
                            (s) =>
                              `${s.revisionId === null ? "Trascrizione" : "Correzione"} #${s.sessionId}`,
                          )
                          .join(" · ")}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </PageSection>
          )}
          <PageSection
            title="Fonti collegate"
            description={`${data.sources.length} riferimenti disponibili su ${runCount(data.run.sourceCount)} fonti. Le fonti eliminate non sono più accessibili.`}
          >
            {data.sources.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {data.sources.map((source) => (
                  <Button
                    key={`${source.sessionId}:${source.revisionId}`}
                    variant="outline"
                    size="sm"
                    disabled={source.excluded}
                    onClick={() => setSource(source)}
                  >
                    {source.revisionId === null ? "Trascrizione" : "Correzione"}{" "}
                    #{source.sessionId}
                    {source.revisionId === null
                      ? ""
                      : ` · Revisione #${source.revisionId}`}
                    {source.excluded ? " · Esclusa" : ""}
                  </Button>
                ))}
              </div>
            )}
          </PageSection>
          <PageSection title="Proposte collegate">
            {data.proposals.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Nessuna proposta collegata disponibile.
              </p>
            ) : (
              <ul className="flex flex-col divide-y text-sm">
                {data.proposals.map((proposal, index) => (
                  <li
                    key={index}
                    className="flex flex-wrap items-center gap-2 py-2"
                  >
                    {proposal.proposalId === null ? (
                      <Badge variant="outline">Proposta eliminata</Badge>
                    ) : (
                      <Button asChild variant="outline" size="xs">
                        <Link
                          to="/review"
                          search={{ proposal: proposal.proposalId }}
                        >
                          Proposta #{proposal.proposalId}
                        </Link>
                      </Button>
                    )}
                    <span>
                      Nel tentativo:{" "}
                      {
                        {
                          created: "creata",
                          updated: "aggiornata",
                          existing: "già esistente",
                        }[proposal.relation]
                      }
                      , revisione {proposal.revision}.
                    </span>
                    <span className="text-muted-foreground">
                      Ora:{" "}
                      {proposal.currentState
                        ? (stateLabels[
                            proposal.currentState as keyof typeof stateLabels
                          ] ?? proposal.currentState)
                        : "non disponibile"}
                      {proposal.currentRevision
                        ? `, revisione ${proposal.currentRevision}`
                        : ""}
                      .
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </PageSection>
          <Collapsible className="flex flex-col gap-3">
            <CollapsibleTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="group/trigger self-start"
              >
                Dettagli tecnici
                <ChevronDownIcon
                  data-icon="inline-end"
                  className="transition-transform group-data-[state=open]/trigger:rotate-180"
                />
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
                <Fact label="Lotto / tentativo">
                  #{batchId} /{" "}
                  {attemptId === null ? "nessuno" : `#${attemptId}`}
                </Fact>
                <Fact label="Modello registrato">
                  {data.run.model ?? "Non disponibile"}
                </Fact>
                <Fact label="Ragionamento registrato">
                  {data.run.reasoningEffort ?? "Non disponibile"}
                </Fact>
                <Fact label="Byte preparati">
                  {runCount(data.run.inputBytes)}
                </Fact>
                <Fact label="Preparazione">{runDate(data.run.createdAt)}</Fact>
                <Fact label="Inizio">{runDate(data.run.startedAt)}</Fact>
                <Fact label="Fine">{runDate(data.run.endedAt)}</Fact>
                <Fact label="Durata richiesta misurata">
                  {runDuration(data.run.elapsedMs)}
                </Fact>
              </dl>
            </CollapsibleContent>
          </Collapsible>
        </>
      )}
      <Dialog
        open={visibleSource !== null}
        onOpenChange={(open) => {
          if (!open) setSource(null);
        }}
      >
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Sessione #{visibleSource?.sessionId}</DialogTitle>
            <DialogDescription>
              Testo corrente e revisioni della fonte.
            </DialogDescription>
          </DialogHeader>
          {visibleSource !== null && (
            <RunSource
              key={`${visibleSource.sessionId}:${visibleSource.revisionId}`}
              {...visibleSource}
            />
          )}
        </DialogContent>
      </Dialog>
    </section>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="break-words">{children}</dd>
    </div>
  );
}
