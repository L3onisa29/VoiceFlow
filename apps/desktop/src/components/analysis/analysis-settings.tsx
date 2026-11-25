import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
} from "react";
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
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { FieldGroup } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import {
  analysisApi,
  analysisErrors,
  analysisReasoningOptions,
  analysisReasoningSchema,
  analysisSettingsSchema,
  type AnalysisSettings as Settings,
  type AnalysisStatus,
} from "@/lib/analysis";
import { toUserMessage } from "@/lib/errors";
import {
  invalidateLocalQueries,
  queryClient,
  queryKeys,
} from "@/lib/query-client";

const limits = [
  { key: "maxSources", label: "Fonti per lotto", min: 1, max: 100 },
  { key: "maxInputBytes", label: "Byte per richiesta", min: 4096, max: 262144 },
  { key: "maxRequestsPerDay", label: "Richieste al giorno", min: 1, max: 20 },
  {
    key: "requestTimeoutSeconds",
    label: "Secondi per richiesta",
    min: 1,
    max: 600,
  },
  { key: "maxSecondsPerDay", label: "Secondi al giorno", min: 1, max: 3600 },
] as const;

type LimitKey = (typeof limits)[number]["key"];
type TextKey = "model" | LimitKey;
type SavingKey = keyof Settings;
type Texts = Partial<Record<TextKey, string>>;

function without(record: Texts, key: TextKey): Texts {
  const next = { ...record };
  delete next[key];
  return next;
}

const range = (min: number, max: number) =>
  `Da ${min.toLocaleString("it-IT")} a ${max.toLocaleString("it-IT")}.`;

export function AnalysisSettings() {
  const [status, setStatus] = useState<AnalysisStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState<SavingKey | null>(null);
  const [confirm, setConfirm] = useState<
    "enabled" | "autoApproveHighConfidence" | null
  >(null);
  // Text being edited in a field; applied on blur or Enter.
  const [drafts, setDrafts] = useState<Texts>({});
  const [errors, setErrors] = useState<Texts>({});
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const inFlight = useRef(false);
  const loaded = useRef(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    void analysisApi
      .status()
      .then((value) => {
        if (!active) return;
        loaded.current = true;
        setStatus(value);
        setDrafts({});
        setErrors({});
        setLoadError(null);
      })
      .catch((error) => {
        if (!active) return;
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

  /**
   * The backend stores consent, model, reasoning and limits together: every
   * change sends the persisted settings merged with `patch`. On failure the
   * persisted status is untouched, so the control shows the saved value again.
   */
  async function apply(key: SavingKey, patch: Partial<Settings>) {
    if (!status || inFlight.current) return false;
    const next = { ...status.settings, ...patch };
    inFlight.current = true;
    setSaving(key);
    try {
      const result = await analysisApi.configure(next, next.enabled);
      setStatus(result);
      void invalidateLocalQueries(queryClient, [queryKeys.analysis]);
      toast.success(
        key === "enabled" && !next.enabled
          ? "Analisi disattivata"
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

  async function commit(key: TextKey) {
    const text = drafts[key];
    if (!status || text === undefined || inFlight.current) return;
    const value = key === "model" ? text.trim() : Number(text);
    if (value === status.settings[key]) {
      setDrafts((current) => without(current, key));
      setErrors((current) => without(current, key));
      return;
    }
    const parsed = analysisSettingsSchema.shape[key].safeParse(
      key !== "model" && text.trim() === "" ? Number.NaN : value,
    );
    if (!parsed.success) {
      const limit = limits.find((item) => item.key === key);
      setErrors((current) => ({
        ...current,
        [key]: limit
          ? `Inserisci un numero intero. ${range(limit.min, limit.max)}`
          : "Usa lettere, numeri, punti, trattini o trattini bassi.",
      }));
      if (limit) setAdvancedOpen(true);
      return;
    }
    setErrors((current) => without(current, key));
    await apply(key, { [key]: parsed.data } as Partial<Settings>);
    setDrafts((current) => without(current, key));
  }

  function textProps(key: TextKey) {
    return {
      disabled: saving !== null,
      "aria-invalid": errors[key] ? true : undefined,
      onBlur: () => void commit(key),
      onKeyDown: (event: KeyboardEvent<HTMLInputElement>) => {
        if (event.key === "Enter") {
          event.preventDefault();
          void commit(key);
        }
        if (event.key === "Escape") {
          setDrafts((current) => without(current, key));
          setErrors((current) => without(current, key));
        }
      },
      onChange: (event: ChangeEvent<HTMLInputElement>) => {
        const text = event.target.value;
        setDrafts((current) => ({ ...current, [key]: text }));
      },
    };
  }

  const busy = saving !== null;
  const settings = status?.settings;

  return (
    <SettingsSection
      id="analysis-settings"
      title="Analisi"
      description="Invia a OpenAI i testi selezionati per proporre correzioni. Ogni modifica si applica subito e interrompe i lotti in corso."
      actions={
        status && (
          <Button
            variant="ghost"
            size="icon"
            aria-label="Aggiorna stato analisi"
            disabled={busy || loading}
            onClick={() => setRefresh((value) => value + 1)}
          >
            {loading ? <Spinner /> : <RefreshCw />}
          </Button>
        )
      }
    >
      {!status || !settings ? (
        loadError ? (
          <Alert variant="destructive">
            <AlertTitle>Preferenze analisi non disponibili</AlertTitle>
            <AlertDescription>{loadError}</AlertDescription>
            <Button
              className="col-start-2 mt-2 justify-self-start"
              variant="outline"
              size="sm"
              disabled={loading}
              onClick={() => setRefresh((value) => value + 1)}
            >
              Riprova analisi
            </Button>
          </Alert>
        ) : (
          <Skeleton className="h-40" />
        )
      ) : (
        <>
          {status.lastError && (
            <Alert>
              <AlertDescription>
                {analysisErrors[status.lastError] ??
                  "Analisi non completata. Aggiorna lo stato prima di riprovare."}
              </AlertDescription>
            </Alert>
          )}
          <SettingsGroup
            title="Invio a OpenAI"
            description={
              status.excludedSessions > 0
                ? `${status.excludedSessions} sessioni escluse dallo Storico.`
                : undefined
            }
          >
            <SettingsRow
              htmlFor="analysis-consent"
              label="Autorizzo l'invio dei testi selezionati a OpenAI tramite Codex CLI"
              description="La dettatura resta sempre locale."
              disabled={busy}
              saving={saving === "enabled"}
            >
              <Checkbox
                id="analysis-consent"
                checked={settings.enabled}
                disabled={busy}
                aria-describedby="analysis-consent-description"
                onCheckedChange={(checked) => {
                  if (checked === true) setConfirm("enabled");
                  else void apply("enabled", { enabled: false });
                }}
              />
            </SettingsRow>
            <SettingsRow
              htmlFor="analysis-auto-approve"
              label="Approva automaticamente con confidenza superiore a 90"
              description="Sotto 91 o senza punteggio serve la tua revisione."
              disabled={busy}
              saving={saving === "autoApproveHighConfidence"}
            >
              <Checkbox
                id="analysis-auto-approve"
                checked={settings.autoApproveHighConfidence}
                disabled={busy}
                aria-describedby="analysis-auto-approve-description"
                onCheckedChange={(checked) => {
                  if (checked === true) setConfirm("autoApproveHighConfidence");
                  else
                    void apply("autoApproveHighConfidence", {
                      autoApproveHighConfidence: false,
                    });
                }}
              />
            </SettingsRow>
          </SettingsGroup>
          <SettingsAdvanced label="Cosa viene inviato e cosa resta">
            <div className="flex flex-col gap-2 text-sm text-muted-foreground">
              <p>
                Trascrizioni originali, testi elaborati, correzioni ricordate e
                definizioni pertinenti del dizionario.
              </p>
              <p>
                Puoi escludere ogni sessione dal dettaglio dello Storico.
                Escludere o eliminare una sessione impedisce nuovi invii, ma non
                cancella i dati già ricevuti da OpenAI né le regole già
                approvate.
              </p>
              <p>
                Una richiesta già accettata da OpenAI può proseguire e avere un
                costo anche dopo una modifica.
              </p>
            </div>
          </SettingsAdvanced>
          <SettingsGroup title="Modello">
            <SettingsRow
              htmlFor="analysis-model"
              label="Modello di analisi"
              description="Predefinito: gpt-6-luna. Deve essere disponibile nel tuo account Codex."
              disabled={busy}
              saving={saving === "model"}
              error={errors.model}
            >
              <Input
                id="analysis-model"
                value={drafts.model ?? settings.model}
                required
                maxLength={128}
                spellCheck={false}
                autoCapitalize="none"
                aria-describedby={
                  errors.model
                    ? "analysis-model-description analysis-model-error"
                    : "analysis-model-description"
                }
                {...textProps("model")}
              />
            </SettingsRow>
            <SettingsRow
              htmlFor="analysis-reasoning"
              label="Ragionamento"
              description="Livelli più alti richiedono più tempo e token."
              disabled={busy}
              saving={saving === "reasoningEffort"}
            >
              <Select
                value={settings.reasoningEffort}
                disabled={busy}
                onValueChange={(value) =>
                  void apply("reasoningEffort", {
                    reasoningEffort: analysisReasoningSchema.parse(value),
                  })
                }
              >
                <SelectTrigger
                  id="analysis-reasoning"
                  className="w-full"
                  aria-describedby="analysis-reasoning-description"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {analysisReasoningOptions.map(({ value, label }) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </SettingsRow>
          </SettingsGroup>
          <SettingsAdvanced
            label="Avanzate · Limiti e utilizzo"
            open={advancedOpen}
            onOpenChange={setAdvancedOpen}
          >
            <div className="flex flex-col gap-4">
              <FieldGroup className="gap-0">
                {limits.map(({ key, label, min, max }) => (
                  <SettingsRow
                    key={key}
                    htmlFor={`analysis-${key}`}
                    label={label}
                    description={range(min, max)}
                    disabled={busy}
                    saving={saving === key}
                    error={errors[key]}
                  >
                    <Input
                      id={`analysis-${key}`}
                      type="number"
                      inputMode="numeric"
                      min={min}
                      max={max}
                      required
                      value={drafts[key] ?? String(settings[key])}
                      aria-describedby={
                        errors[key]
                          ? `analysis-${key}-description analysis-${key}-error`
                          : `analysis-${key}-description`
                      }
                      {...textProps(key)}
                    />
                  </SettingsRow>
                ))}
              </FieldGroup>
              <div
                className="flex flex-col gap-2 text-sm text-muted-foreground"
                aria-label="Contatori analisi"
              >
                <p>
                  Oggi UTC: {status.requestsToday} tentativi ·{" "}
                  {status.inputBytesToday.toLocaleString("it-IT")} byte
                  preparati · {status.elapsedSecondsToday} secondi misurati ·{" "}
                  {status.reservedSecondsToday} secondi riservati
                </p>
                <p>
                  {status.pendingSources} fonti in attesa ·{" "}
                  {status.acceptedCandidates} proposte validate ·{" "}
                  {status.rejectedCandidates} proposte non valide
                </p>
                {status.parkedSources > 0 && (
                  <p>
                    {status.parkedSources} fonti accantonate dopo due tentativi
                    senza proposte valide. Tornano in coda con una
                    configurazione diversa.
                  </p>
                )}
                <p>
                  Una fonte è una trascrizione o una correzione ricordata. Le
                  fonti oltre il limite di byte restano in attesa. I limiti
                  giornalieri si azzerano a mezzanotte UTC.
                </p>
              </div>
            </div>
          </SettingsAdvanced>
          <ConfirmSettingDialog
            open={confirm === "enabled"}
            title="Inviare i testi a OpenAI?"
            description="L'analisi invia a OpenAI, tramite Codex CLI, i testi delle sessioni non escluse."
            details={
              <>
                <p>
                  Include trascrizioni originali, testi elaborati, correzioni
                  ricordate e definizioni pertinenti. La dettatura resta locale.
                </p>
                <p>
                  Puoi escludere singole sessioni dallo Storico. I dati già
                  ricevuti da OpenAI non vengono cancellati.
                </p>
              </>
            }
            confirmLabel="Autorizza invio"
            onClose={() => setConfirm(null)}
            onConfirm={() => void apply("enabled", { enabled: true })}
          />
          <ConfirmSettingDialog
            open={confirm === "autoApproveHighConfidence"}
            title="Approvare automaticamente le regole?"
            description="Le regole valide con confidenza da 91 a 100, senza conflitti o veti manuali, verranno pubblicate senza revisione."
            details={
              <p>
                La confidenza è una stima del provider, non l'accuratezza della
                trascrizione. Vale anche per l'analisi programmata.
              </p>
            }
            confirmLabel="Attiva approvazione"
            onClose={() => setConfirm(null)}
            onConfirm={() =>
              void apply("autoApproveHighConfidence", {
                autoApproveHighConfidence: true,
              })
            }
          />
        </>
      )}
    </SettingsSection>
  );
}
