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
  analysisApi,
  analysisErrors,
  type AnalysisSettings as Settings,
  type AnalysisStatus,
} from "@/lib/analysis";

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
export function AnalysisSettings() {
  const [status, setStatus] = useState<AnalysisStatus | null>(null);
  const [draft, setDraft] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    void analysisApi
      .status()
      .then((value) => {
        if (active) {
          setStatus(value);
          setDraft(value.settings);
          setError(null);
        }
      })
      .catch((error) => {
        if (active) setError(String(error));
      });
    return () => {
      active = false;
    };
  }, [retry]);
  async function save(value: Settings) {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await analysisApi.configure(value, value.enabled);
      setStatus(result);
      setDraft(result.settings);
      setNotice(
        value.enabled
          ? "Consenso e limiti salvati. I suggerimenti richiedono la tua approvazione."
          : "Analisi disattivata. I lotti in corso saranno interrotti.",
      );
    } catch (error) {
      setError(String(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card role="region" aria-labelledby="analysis-settings">
      <CardHeader>
        <CardTitle>
          <h2 id="analysis-settings">Analisi con OpenAI</h2>
        </CardTitle>
        <CardDescription>
          Trova varianti di termini, false partenze e ripetizioni da rivedere
          nei suggerimenti.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {notice && (
          <p role="status" className="text-sm text-muted-foreground">
            {notice}
          </p>
        )}
        {!status || !draft ? (
          error ? (
            <Button
              variant="outline"
              onClick={() => setRetry((value) => value + 1)}
            >
              Riprova analisi
            </Button>
          ) : (
            <Skeleton className="h-40" />
          )
        ) : (
          <>
            <div className="flex flex-wrap gap-2">
              <Badge variant={status.settings.enabled ? "default" : "outline"}>
                {status.settings.enabled
                  ? "Consenso attivo"
                  : "Analisi disattivata"}
              </Badge>
              <Badge variant="secondary">
                {status.excludedSessions} sessioni escluse
              </Badge>
            </div>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void save(draft);
              }}
              className="flex flex-col gap-5"
            >
              <FieldGroup>
                <Field orientation="horizontal">
                  <Checkbox
                    id="analysis-consent"
                    checked={draft.enabled}
                    disabled={busy}
                    onCheckedChange={(checked) =>
                      setDraft({ ...draft, enabled: checked === true })
                    }
                  />
                  <div className="flex flex-col gap-2">
                    <FieldLabel htmlFor="analysis-consent">
                      Autorizzo l'invio dei testi selezionati a OpenAI tramite
                      Codex CLI
                    </FieldLabel>
                    <FieldDescription>
                      Include originali ASR, risultati elaborati e correzioni
                      ricordate delle sessioni non escluse, oltre alle
                      definizioni pertinenti. L'analisi è facoltativa e
                      disattivata inizialmente. Puoi escludere ogni sessione dal
                      dettaglio dello storico.
                    </FieldDescription>
                  </div>
                </Field>
                {limits.map(({ key, label, min, max }) => (
                  <Field key={key}>
                    <FieldLabel htmlFor={`analysis-${key}`}>{label}</FieldLabel>
                    <Input
                      id={`analysis-${key}`}
                      type="number"
                      min={min}
                      max={max}
                      required
                      disabled={busy}
                      value={Number.isNaN(draft[key]) ? "" : draft[key]}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          [key]: event.target.valueAsNumber,
                        })
                      }
                    />
                  </Field>
                ))}
              </FieldGroup>
              <p className="text-sm text-muted-foreground">
                Una fonte è una trascrizione o una correzione ricordata. Le
                fonti oltre il limite di byte restano in attesa. I limiti
                giornalieri si azzerano a mezzanotte UTC. L'invio già accettato
                dal fornitore potrebbe proseguire anche dopo l'interruzione.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button type="submit" disabled={busy}>
                  {busy ? "Salvataggio…" : "Salva preferenze analisi"}
                </Button>
                {status.settings.enabled && (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      void save({ ...status.settings, enabled: false })
                    }
                  >
                    Disattiva analisi
                  </Button>
                )}
                <Button
                  type="button"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => setRetry((value) => value + 1)}
                >
                  Aggiorna stato analisi
                </Button>
              </div>
            </form>
            <div className="flex flex-col gap-2" aria-label="Contatori analisi">
              <p className="text-sm">
                Oggi UTC: {status.requestsToday} tentativi ·{" "}
                {status.inputBytesToday.toLocaleString("it-IT")} byte preparati
                · {status.elapsedSecondsToday} secondi misurati ·{" "}
                {status.reservedSecondsToday} secondi riservati
              </p>
              <p className="text-sm text-muted-foreground">
                {status.pendingSources} fonti in attesa ·{" "}
                {status.acceptedCandidates} proposte validate ·{" "}
                {status.rejectedCandidates} proposte non valide. Nessun costo
                monetario stimato.
              </p>
              {status.lastError && (
                <Alert>
                  <AlertDescription>
                    {analysisErrors[status.lastError] ??
                      "Analisi non completata. Aggiorna lo stato prima di riprovare."}
                  </AlertDescription>
                </Alert>
              )}
              <p className="text-sm text-muted-foreground">
                Escludere o eliminare una sessione impedisce nuovi invii. Non
                elimina i dati già ricevuti da OpenAI. Le regole già approvate
                restano gestibili nei suggerimenti.
              </p>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
