import { useEffect, useState } from "react";
import { createRoute, Link } from "@tanstack/react-router";
import { RefreshCw } from "lucide-react";
import { Route as rootRoute } from "../__root";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Empty,
  EmptyHeader,
  EmptyTitle,
  EmptyDescription,
} from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  confidenceBand,
  confidenceMeaning,
  findingActions,
  findingOutcomes,
} from "@/lib/confidence";
import { HistoryEditor } from "@/components/history/history-editor";
import {
  analysisApi,
  analysisErrors,
  type AnalysisStatus,
} from "@/lib/analysis";
import { schedulerApi, type SchedulerStatus } from "@/lib/scheduler";
import {
  runsApi,
  runStates,
  invocationReasons,
  schedulerReason,
  type RunKey,
  type RunDetail,
  type RunPage,
  type Invocation,
} from "@/lib/analysis-runs";
export const Route = createRoute({
  getParentRoute: () => rootRoute,
  path: "/analysis",
  component: AnalysisPage,
});
const date = (value: number | null) =>
  value === null ? "Non disponibile" : new Date(value).toLocaleString("it-IT");
const count = (value: number | null) =>
  value === null ? "Sconosciuto" : String(value);
const same = (a: RunKey, b: RunKey) =>
  a.batchId === b.batchId && a.attemptId === b.attemptId;
export function AnalysisPage() {
  const [before, setBefore] = useState<RunKey | null>(null);
  const [selected, setSelected] = useState<RunKey | null>(null);
  const [page, setPage] = useState<RunPage | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [status, setStatus] = useState<{
    analysis: AnalysisStatus;
    scheduler: SchedulerStatus;
  } | null>(null);
  const [invocations, setInvocations] = useState<Invocation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [session, setSession] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    let active = true,
      pending = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      if (pending || !active) return;
      if (timer) clearTimeout(timer);
      pending = true;
      setBusy(true);
      let working = false;
      try {
        const results = await Promise.allSettled([
          runsApi.page(before),
          analysisApi.status(),
          schedulerApi.status(),
          runsApi.invocations(),
        ]);
        if (!active) return;
        const [runs, a, s, jobs] = results;
        if (runs.status === "fulfilled") {
          setPage(runs.value);
          working = runs.value.entries.some((r) => r.state === "running");
        }
        if (a.status === "fulfilled" && s.status === "fulfilled") {
          setStatus({ analysis: a.value, scheduler: s.value });
          working ||= s.value.workerRunning;
        }
        if (jobs.status === "fulfilled") setInvocations(jobs.value);
        const failures = results.filter((r) => r.status === "rejected");
        setError(
          failures.length
            ? "Alcuni dati non sono stati aggiornati. Riprova con Aggiorna."
            : null,
        );
        if (selected) {
          try {
            const value = await runsApi.detail(selected);
            if (active) {
              setDetail(value);
              setDetailError(null);
            }
          } catch (e) {
            if (active) {
              setDetail(null);
              setDetailError(String(e));
            }
          }
        }
      } finally {
        pending = false;
        if (active) {
          setBusy(false);
          timer = setTimeout(
            () => {
              void load();
            },
            working ? 3000 : 30000,
          );
        }
      }
    }
    const focus = () => {
      if (!document.hidden) void load();
    };
    void load();
    window.addEventListener("focus", focus);
    document.addEventListener("visibilitychange", focus);
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
      window.removeEventListener("focus", focus);
      document.removeEventListener("visibilitychange", focus);
    };
  }, [before, selected, refresh]);
  function select(key: RunKey) {
    setSelected(key);
    setDetail(null);
    setDetailError(null);
  }
  function changePage(cursor: RunKey | null) {
    setBefore(cursor);
    setPage(null);
    setSelected(null);
    setDetail(null);
  }
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">ATTIVITÀ E RISULTATI</p>
          <h1>Analisi</h1>
          <p>
            Ogni tentativo conserva rilievi ed esiti. La pubblicazione segue il
            criterio di confidenza nelle Impostazioni.
          </p>
        </div>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => setRefresh((n) => n + 1)}
        >
          <RefreshCw data-icon="inline-start" />
          Aggiorna
        </Button>
      </header>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Pianificazione automatica</CardTitle>
          <CardDescription>
            {status
              ? schedulerReason(status.analysis, status.scheduler)
              : "Lettura dello stato…"}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {status && (
            <>
              <div className="flex flex-wrap gap-2">
                <Badge variant="outline">
                  Consenso {status.analysis.settings.enabled ? "attivo" : "OFF"}
                </Badge>
                <Badge variant="outline">
                  Preferenza{" "}
                  {status.scheduler.schedule.enabled ? "attiva" : "OFF"}
                </Badge>
                <Badge variant="outline">
                  Plist {status.scheduler.installed ? "installato" : "assente"}
                </Badge>
                <Badge variant="outline">
                  Job {status.scheduler.loaded ? "caricato" : "non caricato"}
                </Badge>
                <Badge variant="outline">
                  Worker{" "}
                  {status.scheduler.workerRunning ? "attivo" : "inattivo"}
                </Badge>
              </div>
              <p className="text-sm text-muted-foreground">
                {status.analysis.pendingSources} fonti pendenti · fino a{" "}
                {status.analysis.settings.maxSources} fonti per lotto. Il Mac
                deve essere acceso e l'utente connesso. Lo stato del job non
                certifica un'analisi riuscita.
              </p>
            </>
          )}
          <Button asChild variant="outline">
            <Link to="/settings">Apri Impostazioni</Link>
          </Button>
        </CardContent>
      </Card>
      <section
        aria-label="Tentativi di analisi"
        className="flex flex-col gap-3"
      >
        <h2>Tentativi di analisi</h2>
        {!page && busy && <Skeleton className="h-24 w-full" />}
        {page?.entries.length === 0 && (
          <Empty>
            <EmptyHeader>
              <EmptyTitle>Nessuna analisi registrata</EmptyTitle>
              <EmptyDescription>
                I tentativi futuri appariranno qui, anche dopo la chiusura
                dell'app. Le valutazioni esterne non sono esecuzioni dell'app.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        )}
        {page?.entries.map((run) => (
          <Card key={`${run.key.batchId}:${run.key.attemptId}`}>
            <CardHeader>
              <CardTitle>
                Lotto #{run.key.batchId}
                {run.key.attemptId !== null
                  ? ` · Tentativo #${run.key.attemptId}`
                  : ""}
              </CardTitle>
              <CardDescription>
                {date(run.startedAt ?? run.createdAt)}
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-wrap items-center gap-3">
              <Badge variant="outline">
                {runStates[run.state] ?? run.state}
              </Badge>
              <span className="text-sm">
                Fonti: {count(run.sourceCount)} · Accettati:{" "}
                {count(run.accepted)} · Rifiutati: {count(run.rejected)}
              </span>
              <Button
                variant="outline"
                aria-pressed={selected !== null && same(selected, run.key)}
                onClick={() => select(run.key)}
              >
                Dettaglio{" "}
                {run.key.attemptId !== null
                  ? `tentativo #${run.key.attemptId}`
                  : `lotto #${run.key.batchId}`}
              </Button>
            </CardContent>
          </Card>
        ))}
        <div className="flex gap-2">
          {before && (
            <Button variant="outline" onClick={() => changePage(null)}>
              Più recenti
            </Button>
          )}
          {page?.nextCursor && (
            <Button
              variant="outline"
              onClick={() => changePage(page.nextCursor)}
            >
              Tentativi precedenti
            </Button>
          )}
        </div>
      </section>
      {selected && (
        <Card aria-label="Dettaglio analisi">
          <CardHeader>
            <CardTitle>
              Dettaglio lotto #{selected.batchId}
              {selected.attemptId !== null
                ? ` · Tentativo #${selected.attemptId}`
                : ""}
            </CardTitle>
            <CardDescription>
              {detail
                ? (runStates[detail.run.state] ?? detail.run.state)
                : "Caricamento del dettaglio"}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {detailError && (
              <Alert variant="destructive">
                <AlertDescription>
                  {detailError}
                  <Button
                    variant="outline"
                    onClick={() => setRefresh((n) => n + 1)}
                  >
                    Riprova dettaglio
                  </Button>
                </AlertDescription>
              </Alert>
            )}
            {!detail && !detailError && <Skeleton className="h-24 w-full" />}
            {detail && (
              <>
                <dl className="grid grid-cols-2 gap-2 text-sm">
                  <dt>Preparazione lotto</dt>
                  <dd>{date(detail.run.createdAt)}</dd>
                  <dt>Inizio tentativo</dt>
                  <dd>{date(detail.run.startedAt)}</dd>
                  <dt>Fine tentativo</dt>
                  <dd>{date(detail.run.endedAt)}</dd>
                  <dt>Durata richiesta misurata</dt>
                  <dd>
                    {detail.run.elapsedMs === null
                      ? "Sconosciuta"
                      : `${detail.run.elapsedMs} ms`}
                  </dd>
                  <dt>Candidati accettati / rifiutati</dt>
                  <dd>
                    {count(detail.run.accepted)} / {count(detail.run.rejected)}
                  </dd>
                  <dt>Byte preparati</dt>
                  <dd>{count(detail.run.inputBytes)}</dd>
                </dl>
                {detail.run.error && (
                  <Alert>
                    <AlertDescription>
                      {analysisErrors[detail.run.error] ?? detail.run.error}
                    </AlertDescription>
                  </Alert>
                )}
                {detail.run.state === "completed" &&
                  detail.run.accepted === 0 && (
                    <p>Analisi completata senza suggerimenti.</p>
                  )}
                {detail.run.sourceCount === null && (
                  <p className="text-sm text-muted-foreground">
                    Record precedente allo storico dettagliato. Conteggi,
                    risultati e riferimenti mancanti sono sconosciuti.
                  </p>
                )}
                <h3>Sessioni di origine</h3>
                <p className="text-sm text-muted-foreground">
                  {detail.sources.length} riferimenti disponibili su{" "}
                  {count(detail.run.sourceCount)} fonti. Eliminazioni e
                  rimozioni delle revisioni possono ridurli. Le sessioni escluse
                  non vengono aperte da questa pagina.
                </p>
                <div className="flex flex-wrap gap-2">
                  {detail.sources.map((source) => (
                    <Button
                      key={`${source.sessionId}:${source.revisionId}`}
                      variant="outline"
                      disabled={source.excluded}
                      onClick={() => setSession(source.sessionId)}
                    >
                      Sessione #{source.sessionId}
                      {source.revisionId !== null
                        ? ` · Revisione #${source.revisionId}`
                        : ""}
                      {source.excluded ? " · Esclusa" : ""}
                    </Button>
                  ))}
                </div>
                <h3>Rilievi e decisioni</h3>
                <p className="text-sm text-muted-foreground">
                  {confidenceMeaning} Ogni esito descrive questo tentativo; la
                  regola attuale può essere stata disattivata o ripristinata.
                </p>
                {detail.findings.length === 0 && (
                  <p>
                    Nessun rilievo disponibile. I dati precedenti non hanno
                    punteggi retroattivi; eliminazioni ed esclusioni rimuovono i
                    rilievi delle fonti.
                  </p>
                )}
                {detail.findings.map((finding) => (
                  <Card
                    key={finding.id}
                    aria-label={`Rilievo: ${finding.name}`}
                  >
                    <CardHeader>
                      <CardTitle>{finding.name}</CardTitle>
                      <CardDescription>{finding.reason}</CardDescription>
                    </CardHeader>
                    <CardContent className="flex flex-col gap-3">
                      <div className="flex flex-wrap gap-2">
                        <Badge
                          variant="outline"
                          className="max-w-full whitespace-normal"
                        >
                          {findingActions[finding.action]}
                        </Badge>
                        <Badge
                          variant="secondary"
                          className="max-w-full whitespace-normal"
                        >
                          {finding.confidence === null
                            ? "Confidenza sconosciuta"
                            : `Confidenza ${finding.confidence}/100`}{" "}
                          · {confidenceBand(finding.confidence)}
                        </Badge>
                        <Badge
                          variant="outline"
                          className="max-w-full whitespace-normal"
                        >
                          {findingOutcomes[finding.outcome] ?? finding.outcome}
                        </Badge>
                      </div>
                      <dl className="flex flex-col gap-1 text-sm">
                        <dt>Testo della fonte</dt>
                        <dd>{finding.text}</dd>
                        <dt>Risultato consigliato</dt>
                        <dd>{finding.resultText}</dd>
                      </dl>
                      <p className="text-sm text-muted-foreground">
                        Fonti:{" "}
                        {finding.evidence
                          .map(
                            (e) =>
                              `sessione #${e.sessionId}${e.revisionId === null ? "" : `, revisione #${e.revisionId}`}${e.originalIndex === null ? "" : `, segmento ASR ${e.originalIndex + 1}`}`,
                          )
                          .join("; ")}
                      </p>
                      {finding.publication !== null && (
                        <p className="text-sm">
                          Versione al momento dell'esito: {finding.publication}
                        </p>
                      )}
                      {finding.proposalId !== null && (
                        <Button asChild variant="outline">
                          <Link
                            to="/suggestions"
                            search={{ proposal: finding.proposalId }}
                          >
                            Apri proposta #{finding.proposalId}
                          </Link>
                        </Button>
                      )}
                    </CardContent>
                  </Card>
                ))}
                <h3>Proposte collegate</h3>
                <p className="text-sm text-muted-foreground">
                  {detail.proposals.length} proposte distinte. Più candidati
                  possono riferirsi alla stessa proposta. Il collegamento apre
                  la versione attuale, che può essere stata modificata dopo il
                  tentativo.
                </p>
                <div className="flex flex-wrap gap-2">
                  {detail.proposals.map((proposal, i) =>
                    proposal.proposalId === null ? (
                      <Badge variant="outline" key={i}>
                        Proposta eliminata · revisione {proposal.revision}
                      </Badge>
                    ) : (
                      <Button key={i} asChild variant="outline">
                        <Link
                          to="/suggestions"
                          search={{ proposal: proposal.proposalId }}
                        >
                          Proposta #{proposal.proposalId} ·{" "}
                          {
                            {
                              created: "creata",
                              updated: "aggiornata",
                              existing: "già esistente",
                            }[proposal.relation]
                          }{" "}
                          · revisione {proposal.revision}
                        </Link>
                      </Button>
                    ),
                  )}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Avvii del job locale</CardTitle>
          <CardDescription>
            Ultimi 100 avvii che hanno acquisito il worker. I tentativi di
            analisi sopra restano nello storico senza questa scadenza. Nessun
            avvio elencato certifica l'invio al provider.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {invocations.length === 0 ? (
            <p>Nessun avvio registrato.</p>
          ) : (
            invocations.map((job) => (
              <div key={job.id} className="flex flex-wrap gap-2 text-sm">
                <time>{date(job.startedAt)}</time>
                <Badge variant="outline">
                  {invocationReasons[job.reason] ?? job.reason}
                </Badge>
                {job.batchId !== null && <span>Lotto #{job.batchId}</span>}
                {job.endedAt === null && <span>Fine non registrata</span>}
              </div>
            ))
          )}
        </CardContent>
      </Card>
      <Dialog
        open={session !== null}
        onOpenChange={(open) => {
          if (!open && !editing) setSession(null);
        }}
      >
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Sessione #{session}</DialogTitle>
            <DialogDescription>
              Dettaglio corrente dello storico. Le revisioni sono consultabili
              qui.
            </DialogDescription>
          </DialogHeader>
          {session !== null && (
            <HistoryEditor
              key={session}
              id={session}
              onBusy={setEditing}
              onChanged={() => setRefresh((n) => n + 1)}
            />
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
