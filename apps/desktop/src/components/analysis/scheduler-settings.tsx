import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { ConfirmSettingDialog } from "@/components/settings/confirm-setting-dialog";
import { SettingsAdvanced } from "@/components/settings/settings-advanced";
import { SettingsRow } from "@/components/settings/settings-row";
import {
  SettingsGroup,
  SettingsSection,
} from "@/components/settings/settings-section";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { toUserMessage } from "@/lib/errors";
import {
  invalidateLocalQueries,
  queryClient,
  queryKeys,
} from "@/lib/query-client";
import {
  minuteValue,
  schedulerApi,
  timeValue,
  windowError,
  type Schedule,
  type SchedulerStatus,
} from "@/lib/scheduler";

type SavingKey = "enabled" | "startMinute" | "endMinute" | "repair";
type TimeKey = "startMinute" | "endMinute";
type Texts = Partial<Record<TimeKey, string>>;

function without(record: Texts, key: TimeKey): Texts {
  const next = { ...record };
  delete next[key];
  return next;
}

export function SchedulerSettings() {
  const [status, setStatus] = useState<SchedulerStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState<SavingKey | null>(null);
  const [confirm, setConfirm] = useState(false);
  // Text being edited in a time field; applied on blur or Enter.
  const [drafts, setDrafts] = useState<Texts>({});
  const [errors, setErrors] = useState<Texts>({});
  const inFlight = useRef(false);
  const loaded = useRef(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    void schedulerApi
      .status()
      .then((value) => {
        if (!active) return;
        loaded.current = true;
        setStatus(value);
        setDrafts({});
        setErrors({});
        setLoadError(null);
        void invalidateLocalQueries(queryClient, [queryKeys.analysis]);
      })
      .catch((error) => {
        if (!active) return;
        // With a status already shown the page stays usable: report only.
        if (loaded.current) toast.error(toUserMessage(error));
        else setLoadError(toUserMessage(error));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [refresh]);

  /** Sends the full schedule; consent follows the requested `enabled`. */
  async function apply(key: SavingKey, schedule: Schedule) {
    if (inFlight.current) return false;
    inFlight.current = true;
    setSaving(key);
    try {
      const value = await schedulerApi.configure(schedule, schedule.enabled);
      setStatus(value);
      // A partial launchd failure stays visible in the inline alert.
      if (!value.lastError)
        toast.success(
          key === "enabled" && !schedule.enabled
            ? "Analisi programmata disattivata"
            : "Impostazione salvata",
        );
      return true;
    } catch (error) {
      toast.error(toUserMessage(error));
      return false;
    } finally {
      inFlight.current = false;
      setSaving(null);
    }
  }

  async function commitTime(key: TimeKey) {
    const text = drafts[key];
    if (!status || text === undefined || inFlight.current) return;
    const minute = minuteValue(text);
    if (minute === status.schedule[key]) {
      setDrafts((current) => without(current, key));
      setErrors((current) => without(current, key));
      return;
    }
    const next = { ...status.schedule, [key]: minute };
    const invalid = windowError(next.startMinute, next.endMinute);
    if (invalid) {
      setErrors((current) => ({ ...current, [key]: invalid }));
      return;
    }
    setErrors((current) => without(current, key));
    await apply(key, next);
    // Applied or reverted: the field shows the persisted value again.
    setDrafts((current) => without(current, key));
  }

  const busy = saving !== null;
  const healthy =
    status !== null && status.loaded && status.matchesConfiguration;

  return (
    <SettingsSection
      id="scheduler-settings"
      title="Automazione"
      description="Analisi programmata nella fascia scelta, anche a finestra chiusa, con il Mac acceso e l'utente connesso."
      actions={
        status && (
          <>
            {status.schedule.enabled && (
              <Badge variant={healthy ? "success" : "destructive"}>
                {healthy ? "Pronta" : "Da riparare"}
              </Badge>
            )}
            {status.workerRunning && (
              <Badge variant="secondary">Analisi in corso</Badge>
            )}
            {status.recording && (
              <Badge variant="secondary">Dettatura prioritaria attiva</Badge>
            )}
            <Button
              variant="ghost"
              size="icon"
              aria-label="Aggiorna stato pianificazione"
              disabled={busy || loading}
              onClick={() => setRefresh((value) => value + 1)}
            >
              {loading ? <Spinner /> : <RefreshCw />}
            </Button>
          </>
        )
      }
    >
      {!status ? (
        loadError ? (
          <Alert variant="destructive">
            <AlertTitle>Pianificazione non disponibile</AlertTitle>
            <AlertDescription>{loadError}</AlertDescription>
            <Button
              className="col-start-2 mt-2 justify-self-start"
              variant="outline"
              size="sm"
              disabled={loading}
              onClick={() => setRefresh((value) => value + 1)}
            >
              Riprova pianificazione
            </Button>
          </Alert>
        ) : (
          <Skeleton className="h-40" />
        )
      ) : (
        <>
          {status.lastError && (
            <Alert variant="destructive">
              <AlertTitle>
                {status.schedule.enabled
                  ? "Pianificazione non applicata"
                  : "Rimozione non completata"}
              </AlertTitle>
              <AlertDescription>{status.lastError}</AlertDescription>
              <Button
                className="col-start-2 mt-2 justify-self-start"
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => void apply("repair", status.schedule)}
              >
                {saving === "repair" && <Spinner data-icon="inline-start" />}
                {status.schedule.enabled
                  ? "Ripara pianificazione"
                  : "Riprova disattivazione"}
              </Button>
            </Alert>
          )}
          {status.schedule.enabled && !healthy && !status.lastError && (
            <Alert>
              <AlertTitle>L'analisi programmata non è pronta</AlertTitle>
              <AlertDescription>
                La programmazione di macOS non corrisponde alla preferenza
                salvata.
              </AlertDescription>
              <Button
                className="col-start-2 mt-2 justify-self-start"
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => void apply("repair", status.schedule)}
              >
                {saving === "repair" && <Spinner data-icon="inline-start" />}
                Ripara pianificazione
              </Button>
            </Alert>
          )}
          {!status.helperAvailable && (
            <Alert>
              <AlertTitle>Componente di analisi non disponibile</AlertTitle>
              <AlertDescription>
                Apri una versione completa e firmata di VoiceFlow per attivare
                la pianificazione.
              </AlertDescription>
            </Alert>
          )}
          <SettingsGroup>
            <SettingsRow
              htmlFor="scheduler-consent"
              label="Analisi programmata"
              description="Usa il consenso OpenAI e l'approvazione automatica impostati in Analisi."
              disabled={busy || !status.helperAvailable}
              saving={saving === "enabled"}
            >
              <Checkbox
                id="scheduler-consent"
                checked={status.schedule.enabled}
                disabled={busy || !status.helperAvailable}
                aria-describedby="scheduler-consent-description"
                onCheckedChange={(checked) => {
                  if (checked === true) setConfirm(true);
                  else
                    void apply("enabled", {
                      ...status.schedule,
                      enabled: false,
                    });
                }}
              />
            </SettingsRow>
            {(
              [
                ["startMinute", "scheduler-start", "Inizio fascia"],
                ["endMinute", "scheduler-end", "Fine fascia"],
              ] as const
            ).map(([key, id, label]) => (
              <SettingsRow
                key={key}
                htmlFor={id}
                label={label}
                description={
                  key === "endMinute"
                    ? "Da 1 minuto a 12 ore. Se precede l'inizio, termina il giorno dopo."
                    : "Ora locale del Mac."
                }
                disabled={busy}
                saving={saving === key}
                error={errors[key]}
              >
                <Input
                  id={id}
                  type="time"
                  required
                  disabled={busy}
                  aria-invalid={errors[key] ? true : undefined}
                  aria-describedby={
                    errors[key]
                      ? `${id}-description ${id}-error`
                      : `${id}-description`
                  }
                  value={drafts[key] ?? timeValue(status.schedule[key])}
                  onChange={(event) => {
                    const text = event.target.value;
                    setDrafts((current) => ({ ...current, [key]: text }));
                  }}
                  onBlur={() => void commitTime(key)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      void commitTime(key);
                    }
                    if (event.key === "Escape") {
                      setDrafts((current) => without(current, key));
                      setErrors((current) => without(current, key));
                    }
                  }}
                />
              </SettingsRow>
            ))}
          </SettingsGroup>
          <SettingsAdvanced label="Avanzate · Diagnostica automazione">
            <div className="flex flex-col gap-2 text-sm text-muted-foreground">
              <p>
                {status.loaded
                  ? "Programmazione rilevata in macOS."
                  : "Programmazione non rilevata in macOS."}
              </p>
              {status.phase === "interrupted" && (
                <p>
                  Ultima analisi interrotta: le fonti in attesa riprendono al
                  prossimo avvio consentito.
                </p>
              )}
              {status.phase === "deferred" && (
                <p>Analisi rinviata dopo un errore.</p>
              )}
              <p>
                Tentativi falliti o interrotti oggi (UTC): {status.retryCount}{" "}
                su 3, a distanza di almeno 5 minuti.
              </p>
              {status.nextAttempt > 0 && (
                <p>
                  Prossimo tentativo non prima del{" "}
                  {new Date(status.nextAttempt).toLocaleString("it-IT")}.
                </p>
              )}
              {status.workerRunning && status.leaseUntil > 0 && (
                <p>
                  Scadenza massima dell'analisi:{" "}
                  {new Date(status.leaseUntil).toLocaleString("it-IT")}.
                </p>
              )}
              <p>
                La dettatura interrompe l'analisi. Interrompere i processi
                locali non annulla una richiesta già accettata da OpenAI né il
                suo costo.
              </p>
            </div>
          </SettingsAdvanced>
          <ConfirmSettingDialog
            open={confirm}
            title="Attivare l'analisi programmata?"
            description={`VoiceFlow avvierà l'analisi ogni giorno tra le ${timeValue(status.schedule.startMinute)} e le ${timeValue(status.schedule.endMinute)}, anche a finestra chiusa.`}
            details={
              <>
                <p>
                  Invia a OpenAI solo se il consenso in Analisi è attivo e
                  applica la stessa approvazione automatica delle regole.
                </p>
                <p>
                  Ogni richiesta rientra nei limiti giornalieri dell'analisi e
                  può avere un costo.
                </p>
              </>
            }
            confirmLabel="Attiva"
            onClose={() => setConfirm(false)}
            onConfirm={() =>
              void apply("enabled", { ...status.schedule, enabled: true })
            }
          />
        </>
      )}
    </SettingsSection>
  );
}
