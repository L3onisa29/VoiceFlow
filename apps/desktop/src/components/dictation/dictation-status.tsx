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
        <div className="accessibility-row">
          <span>
            {state.modelPresent
              ? "Il modello è disponibile sul Mac."
              : "Il modello NVIDIA non è ancora installato su questo Mac."}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={!state.modelPresent || busy}
            onClick={() => void onLoadModel()}
          >
            Carica modello
          </Button>
        </div>
      )}
      {state?.message && state.phase !== "error" && (
        <p className="outcome-message" role="status">
          {state.message}
        </p>
      )}
    </>
  );
}
