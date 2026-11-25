import { Cpu, Info } from "lucide-react";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useDictation, useDictationState } from "@/hooks/use-dictation";
import { selectProcessing } from "@/stores/dictation";

/** Engine notices inside the session card: preparation, model, last message. */
export function DictationStatus() {
  const state = useDictationState(
    "phase",
    "modelReady",
    "modelPresent",
    "message",
  );
  const { busy, execute } = useDictation((s) => ({
    busy: s.busy,
    execute: s.execute,
  }));
  const processing = useDictation(selectProcessing);
  const onLoadModel = () => {
    void execute("load_model");
  };
  return (
    <>
      {state?.phase === "loading" && (
        <Alert role="status">
          <Spinner aria-label="Preparazione in corso" />
          <AlertTitle>
            {state.modelReady
              ? "Preparazione della dettatura"
              : "Caricamento del modello"}
          </AlertTitle>
          <AlertDescription>
            Attendi prima di parlare: la registrazione inizia quando lo stato
            diventa "In ascolto".
          </AlertDescription>
        </Alert>
      )}
      {state && !state.modelReady && !processing && (
        <Alert role="status">
          <Cpu aria-hidden="true" />
          <AlertTitle>Motore locale</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
            <span>
              {state.modelPresent
                ? "Il modello è sul Mac ma non è caricato."
                : "Il modello NVIDIA non è installato su questo Mac."}
            </span>
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
      {state?.message &&
        state.phase !== "error" &&
        state.phase !== "loading" && (
          <Alert role="status">
            <Info aria-hidden="true" />
            <AlertDescription>{state.message}</AlertDescription>
          </Alert>
        )}
    </>
  );
}
