import { useEffect, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  schedulerApi,
  timeValue,
  minuteValue,
  type Schedule,
  type SchedulerStatus,
} from "@/lib/scheduler";
export function SchedulerSettings() {
  const [status, setStatus] = useState<SchedulerStatus | null>(null);
  const [draft, setDraft] = useState<Schedule | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    void schedulerApi
      .status()
      .then((value) => {
        if (active) {
          setStatus(value);
          setDraft(value.schedule);
          setError(null);
        }
      })
      .catch((error) => {
        if (active) setError(String(error));
      });
    return () => {
      active = false;
    };
  }, [refresh]);
  async function save(schedule: Schedule) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const value = await schedulerApi.configure(schedule, schedule.enabled);
      setStatus(value);
      setDraft(value.schedule);
    } catch (error) {
      setError(String(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card role="region" aria-labelledby="scheduler-settings">
      <CardHeader>
        <CardTitle>
          <h2 id="scheduler-settings">Pianificazione locale</h2>
        </CardTitle>
        <CardDescription>
          Lavora anche con la finestra chiusa, durante la fascia scelta e con il
          Mac acceso e l'utente connesso.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {!status || !draft ? (
          error ? (
            <Button variant="outline" onClick={() => setRefresh((v) => v + 1)}>
              Riprova pianificazione
            </Button>
          ) : (
            <Skeleton className="h-40" />
          )
        ) : (
          <>
            <div className="flex flex-wrap gap-2">
              <Badge variant={status.schedule.enabled ? "default" : "outline"}>
                {status.schedule.enabled
                  ? "Pianificazione richiesta"
                  : "Pianificazione disattivata"}
              </Badge>
              <Badge variant="secondary">
                {status.loaded
                  ? "Job caricato in macOS"
                  : "Job non rilevato in macOS"}
              </Badge>
              <Badge variant="outline">
                {status.workerRunning ? "Worker in esecuzione" : "Worker fermo"}
              </Badge>
            </div>
            {status.lastError && (
              <Alert variant="destructive">
                <AlertDescription>{status.lastError}</AlertDescription>
              </Alert>
            )}
            {status.schedule.enabled &&
              (!status.loaded || !status.matchesConfiguration) && (
                <Alert>
                  <AlertDescription>
                    La configurazione richiesta non coincide con il job
                    installato. Usa Ripara pianificazione.
                  </AlertDescription>
                </Alert>
              )}
            {!status.helperAvailable && (
              <Alert>
                <AlertDescription>
                  Helper non disponibile. Apri una build firmata completa di
                  VoiceFlow per installare il job.
                </AlertDescription>
              </Alert>
            )}
            <form
              className="flex flex-col gap-5"
              onSubmit={(event) => {
                event.preventDefault();
                void save(draft);
              }}
            >
              <FieldGroup>
                <Field orientation="horizontal">
                  <Checkbox
                    id="scheduler-consent"
                    checked={draft.enabled}
                    disabled={busy || !status.helperAvailable}
                    onCheckedChange={(checked) =>
                      setDraft({ ...draft, enabled: checked === true })
                    }
                  />
                  <div className="flex flex-col gap-2">
                    <FieldLabel htmlFor="scheduler-consent">
                      Attiva il LaunchAgent per l'analisi pianificata
                    </FieldLabel>
                    <FieldDescription>
                      Richiede anche il consenso OpenAI salvato sopra. Le
                      proposte restano da approvare nei Suggerimenti.
                    </FieldDescription>
                  </div>
                </Field>
                <Field>
                  <FieldLabel htmlFor="scheduler-start">
                    Inizio fascia locale
                  </FieldLabel>
                  <Input
                    id="scheduler-start"
                    type="time"
                    required
                    disabled={busy}
                    value={timeValue(draft.startMinute)}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        startMinute: minuteValue(e.target.value),
                      })
                    }
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="scheduler-end">
                    Fine fascia locale
                  </FieldLabel>
                  <Input
                    id="scheduler-end"
                    type="time"
                    required
                    disabled={busy}
                    value={timeValue(draft.endMinute)}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        endMinute: minuteValue(e.target.value),
                      })
                    }
                  />
                </Field>
              </FieldGroup>
              <p className="text-sm text-muted-foreground">
                Da 1 minuto a 12 ore. Se la fine precede l'inizio, la fascia
                termina il giorno seguente. Segue il fuso locale del Mac. Un
                risveglio fuori fascia non avvia analisi; durante la dettatura
                il lavoro viene interrotto, anche in pausa e finalizzazione.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button disabled={busy} type="submit">
                  Salva pianificazione
                </Button>
                {status.schedule.enabled && (
                  <>
                    <Button
                      type="button"
                      variant="outline"
                      disabled={busy}
                      onClick={() => void save(status.schedule)}
                    >
                      Ripara pianificazione
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        void save({ ...status.schedule, enabled: false })
                      }
                    >
                      Disattiva pianificazione
                    </Button>
                  </>
                )}
                <Button
                  type="button"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => setRefresh((v) => v + 1)}
                >
                  Aggiorna stato pianificazione
                </Button>
              </div>
            </form>
            <p className="text-sm text-muted-foreground">
              {status.recording
                ? "Dettatura prioritaria attiva."
                : "Nessuna dettatura rilevata."}{" "}
              {status.phase === "interrupted"
                ? "Ultimo worker interrotto; recupero dei checkpoint al prossimo avvio consentito."
                : status.phase === "deferred"
                  ? "Analisi rinviata dopo un errore."
                  : ""}{" "}
              Tentativi falliti o interrotti oggi UTC: {status.retryCount}.
              Massimo 3 al giorno, attesa di almeno 5 minuti fra retry, entro i
              limiti dell'analisi.
            </p>
            {status.nextAttempt > 0 && (
              <p className="text-sm text-muted-foreground">
                Nuovo tentativo non prima del{" "}
                {new Date(status.nextAttempt).toLocaleString("it-IT")}, sempre
                entro la fascia scelta e i limiti disponibili.
              </p>
            )}
            {status.workerRunning && status.leaseUntil > 0 && (
              <p className="text-sm text-muted-foreground">
                Scadenza massima del worker:{" "}
                {new Date(status.leaseUntil).toLocaleString("it-IT")}. Il blocco
                del processo impedisce sovrapposizioni anche se l'orario risulta
                scaduto.
              </p>
            )}
            <p className="text-sm text-muted-foreground">
              L'interruzione dei processi locali non annulla una richiesta già
              accettata da OpenAI e non garantisce l'annullamento del costo.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
