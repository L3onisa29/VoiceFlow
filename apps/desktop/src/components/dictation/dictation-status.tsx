import { Cpu } from "lucide-react";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { DictationState } from "@/lib/desktop";

type DictationStatusProps = {
  state: DictationState | null;
  busy: boolean;
  processing: boolean;
  onLoadModel: () => void;
};

export function DictationStatus({
  state,
  busy,
  processing,
  onLoadModel,
}: DictationStatusProps) {
  return (
    <>
      {state && !state.modelReady && !processing && (
        <Alert role="status">
          <Cpu aria-hidden="true" />
          <AlertTitle>Motore locale</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
            <p>
              {state.modelPresent
                ? "Il modello è disponibile sul Mac."
                : "Il modello NVIDIA non è ancora installato su questo Mac."}
            </p>
            <Button
              size="sm"
              variant="outline"
              disabled={!state.modelPresent || busy}
              onClick={onLoadModel}
            >
              Carica modello
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {state?.message && state.phase !== "error" && (
        <Alert role="status">
          <AlertDescription>{state.message}</AlertDescription>
        </Alert>
      )}
    </>
  );
}
