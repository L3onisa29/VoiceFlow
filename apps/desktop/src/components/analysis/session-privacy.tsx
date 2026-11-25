import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
} from "@/components/ui/field";
import { Spinner } from "@/components/ui/spinner";
import { analysisApi } from "@/lib/analysis";
import { toUserMessage } from "@/lib/errors";
import { invalidatePrivacy } from "@/lib/query-client";

/** Per-session exclusion from OpenAI analysis. Applies on change. */
export function SessionPrivacy({
  id,
  disabled,
}: {
  id: number;
  disabled: boolean;
}) {
  const [excluded, setExcluded] = useState<boolean | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    void analysisApi
      .excluded(id)
      .then((value) => {
        if (!active) return;
        setExcluded(value);
        setLoadError(null);
      })
      .catch((error) => {
        if (active) setLoadError(toUserMessage(error));
      });
    return () => {
      active = false;
    };
  }, [id, retry]);

  async function save(value: boolean) {
    setBusy(true);
    try {
      await analysisApi.exclude(id, value);
      setExcluded(value);
      await invalidatePrivacy();
    } catch (error) {
      // The checkbox keeps the persisted value.
      toast.error(toUserMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const controlId = `analysis-exclude-${id}`;
  return (
    <Field orientation="horizontal" data-disabled={disabled || busy}>
      <Checkbox
        id={controlId}
        checked={excluded ?? false}
        disabled={disabled || busy || excluded === null}
        aria-describedby={`${controlId}-description`}
        onCheckedChange={(value) => void save(value === true)}
      />
      <FieldContent>
        <FieldLabel htmlFor={controlId}>
          Escludi questa sessione dall'analisi con OpenAI
        </FieldLabel>
        <FieldDescription id={`${controlId}-description`}>
          Vale anche per le correzioni ricordate. Non rimuove i testi già
          inviati.
        </FieldDescription>
        {loadError && excluded === null && (
          <Alert variant="destructive">
            <AlertDescription>{loadError}</AlertDescription>
            <Button
              className="col-start-2 mt-2 justify-self-start"
              variant="outline"
              size="sm"
              onClick={() => {
                setLoadError(null);
                setRetry((value) => value + 1);
              }}
            >
              Riprova privacy sessione
            </Button>
          </Alert>
        )}
      </FieldContent>
      {busy && <Spinner aria-label="Salvataggio in corso" />}
    </Field>
  );
}
