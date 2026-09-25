import { useEffect, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { analysisApi } from "@/lib/analysis";
export function SessionPrivacy({
  id,
  disabled,
}: {
  id: number;
  disabled: boolean;
}) {
  const [excluded, setExcluded] = useState<boolean | null>(null);
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    void analysisApi
      .excluded(id)
      .then((value) => {
        if (active) setExcluded(value);
      })
      .catch((error) => {
        if (active) setError(String(error));
      });
    return () => {
      active = false;
    };
  }, [id, retry]);
  async function save(value: boolean) {
    setBusy(true);
    setError(null);
    try {
      await analysisApi.exclude(id, value);
      setExcluded(value);
    } catch (error) {
      setError(String(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Field>
      <div className="flex items-center gap-3">
        <Checkbox
          id={`analysis-exclude-${id}`}
          checked={excluded ?? false}
          disabled={disabled || busy || excluded === null}
          onCheckedChange={(value) => void save(value === true)}
        />
        <FieldLabel htmlFor={`analysis-exclude-${id}`}>
          Escludi questa sessione dall'analisi con OpenAI
        </FieldLabel>
      </div>
      <FieldDescription>
        L'esclusione vale anche per le correzioni ricordate. Non rimuove i testi
        già inviati al fornitore.
      </FieldDescription>
      {error && excluded === null && (
        <Button
          variant="outline"
          onClick={() => {
            setError(null);
            setRetry((value) => value + 1);
          }}
        >
          Riprova privacy sessione
        </Button>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
    </Field>
  );
}
