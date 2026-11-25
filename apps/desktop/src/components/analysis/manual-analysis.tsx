import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { PlayIcon } from "lucide-react";
import { toast } from "sonner";
import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import { analysisApi } from "@/lib/analysis";
import {
  invocationReasons,
  runsApi,
  runStates,
  type Invocation,
  type RunKey,
} from "@/lib/analysis-runs";
import { toUserMessage } from "@/lib/errors";
import { queryKeys } from "@/lib/query-client";
import { schedulerApi } from "@/lib/scheduler";
import { useRunResource } from "./use-run-resource";

const isActive = (job: Invocation | undefined | null) =>
  job?.reason === "requested" || job?.reason === "running";

async function loadManual() {
  const [analysis, scheduler, invocations] = await Promise.all([
    analysisApi.status(),
    schedulerApi.status(),
    runsApi.invocations(),
  ]);
  return { status: { analysis, scheduler }, invocations };
}
const manualInterval = (data: Awaited<ReturnType<typeof loadManual>>) =>
  data.status.scheduler.workerRunning ||
  data.invocations.some((job) => job.origin === "manual" && isActive(job))
    ? 3000
    : null;

/**
 * State of the manual "Esegui analisi ora" action, shared by the header button
 * and the status notice of the review layout.
 */
export function useManualAnalysis() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const load = useCallback(loadManual, []);
  const { data, error, refresh } = useRunResource(
    [...queryKeys.analysis, "manual"],
    load,
    manualInterval,
  );
  const lock = useRef(false);
  const [sending, setSending] = useState(false);
  const [request, setRequest] = useState<Invocation | null>(null);
  const latest = data?.invocations.find((job) => job.origin === "manual");
  const job = request && (!latest || request.id > latest.id) ? request : latest;
  const active = isActive(job);
  const status = data?.status ?? null;
  const blocked = !status
    ? error
      ? "Requisiti di avvio non leggibili."
      : "Lettura dei requisiti di avvio…"
    : !status.analysis.settings.enabled
      ? "Attiva il consenso OpenAI nelle Impostazioni."
      : !status.scheduler.helperAvailable
        ? "Helper non disponibile. Apri la build completa di VoiceFlow."
        : status.scheduler.recording
          ? "Attendi la fine della dettatura."
          : status.scheduler.workerRunning
            ? "Un'analisi è già in corso."
            : null;

  const openRun = useCallback(
    (key: RunKey) => {
      const search = {
        selectedBatch: key.batchId,
        selectedAttempt: key.attemptId ?? undefined,
      };
      if (key.attemptId === null)
        void navigate({
          to: "/review/runs/$batchId/prepared",
          params: { batchId: key.batchId },
          search,
        });
      else
        void navigate({
          to: "/review/runs/$batchId/attempts/$attemptId",
          params: { batchId: key.batchId, attemptId: key.attemptId },
          search,
        });
    },
    [navigate],
  );

  // The outcome of a request made here is announced once, when it ends.
  const watched = useRef<number | null>(null);
  useEffect(() => {
    if (!job || watched.current !== job.id || isActive(job)) return;
    watched.current = null;
    announceOutcome(job, openRun);
  }, [job, openRun]);

  async function run() {
    if (lock.current) return false;
    lock.current = true;
    setSending(true);
    try {
      const requested = await runsApi.runNow();
      watched.current = requested.id;
      setRequest(requested);
      void client.invalidateQueries({ queryKey: queryKeys.analysis });
      return true;
    } catch (error) {
      toast.error(toUserMessage(error));
      return false;
    } finally {
      lock.current = false;
      setSending(false);
    }
  }
  return {
    status,
    error,
    refresh,
    job,
    active,
    blocked,
    sending,
    run,
    openRun,
  };
}
export type ManualAnalysisState = ReturnType<typeof useManualAnalysis>;

function announceOutcome(job: Invocation, openRun: (key: RunKey) => void) {
  const action =
    job.batchId !== null && job.attemptId !== null
      ? {
          label: "Apri esito",
          onClick: () =>
            openRun({ batchId: job.batchId!, attemptId: job.attemptId }),
        }
      : undefined;
  switch (job.reason) {
    case "completed":
      toast.success("Analisi conclusa", {
        description: "Esiti e proposte sono in Esecuzioni.",
        action,
      });
      return;
    case "partial":
      toast.warning("Analisi conclusa con avvisi", { action });
      return;
    case "failed":
    case "interrupted":
    case "cancelled":
      toast.error("Analisi non completata", {
        description: `${runStates[job.reason]}.`,
        action,
      });
      return;
    case "no-work":
      toast.info("Nessuna fonte da analizzare", {
        description: invocationReasons["no-work"],
      });
      return;
    default:
      toast.warning("Analisi non avviata", {
        description: invocationReasons[job.reason] ?? "Riprova più tardi.",
        action,
      });
  }
}

/** Header action: confirms the effect before requesting one manual batch. */
export function ManualAnalysisButton({
  manual,
}: {
  manual: ManualAnalysisState;
}) {
  const [open, setOpen] = useState(false);
  const { status, error, refresh, active, blocked, sending, run } = manual;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button disabled={active}>
          {active ? (
            <Spinner data-icon="inline-start" aria-hidden="true" />
          ) : (
            <PlayIcon data-icon="inline-start" />
          )}
          Esegui analisi ora
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="flex w-80 flex-col gap-3">
        <PopoverHeader>
          <PopoverTitle>Eseguire l'analisi ora?</PopoverTitle>
          <PopoverDescription>
            Analizza un lotto di fonti pendenti, anche fuori fascia. Consenso,
            limiti e attese dopo gli errori restano validi.
          </PopoverDescription>
        </PopoverHeader>
        {status && (
          <p className="text-sm" data-testid="manual-autoapproval">
            {status.analysis.settings.autoApproveHighConfidence
              ? "Approvazione automatica attiva: le proposte con confidenza sopra 90 possono pubblicare regole."
              : "Approvazione automatica disattivata: le nuove proposte restano da revisionare."}
          </p>
        )}
        {blocked && (
          <Alert variant={error ? "destructive" : "default"}>
            <AlertDescription>{blocked}</AlertDescription>
            {error && (
              <AlertAction>
                <Button variant="outline" size="xs" onClick={refresh}>
                  Riprova
                </Button>
              </AlertAction>
            )}
          </Alert>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setOpen(false)}>
            Annulla
          </Button>
          <Button
            size="sm"
            disabled={sending || active || !!blocked}
            onClick={() =>
              void run().then((sent) => {
                if (sent) setOpen(false);
              })
            }
          >
            {sending && <Spinner data-icon="inline-start" aria-hidden="true" />}
            Avvia analisi
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

/** Compact notice while a manual request is pending or running. */
export function ManualAnalysisStatus({
  manual,
}: {
  manual: ManualAnalysisState;
}) {
  const { job, active, openRun } = manual;
  if (!job || !active) return null;
  return (
    <section aria-label="Avvio manuale">
      <Alert role="status">
        <Spinner aria-hidden="true" />
        <AlertTitle>
          {job.attemptId === null
            ? "Richiesta di avvio ricevuta"
            : "Analisi in esecuzione"}
        </AlertTitle>
        <AlertDescription>
          Avvio manuale #{job.id}. L'esito compare al termine.
        </AlertDescription>
        {job.batchId !== null && job.attemptId !== null && (
          <AlertAction>
            <Button
              variant="outline"
              size="xs"
              onClick={() =>
                openRun({ batchId: job.batchId!, attemptId: job.attemptId })
              }
            >
              Apri esito
            </Button>
          </AlertAction>
        )}
      </Alert>
    </section>
  );
}
