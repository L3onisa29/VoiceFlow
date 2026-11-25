import { ArrowUpRight, Mic, Keyboard, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { useDictation, useDictationState } from "@/hooks/use-dictation";
import { toUserMessage } from "@/lib/errors";

/** Only actionable problems: errors and missing macOS permissions. */
export function DictationAlerts() {
  const state = useDictationState("phase", "message", "modelReady");
  const { permissions, error, onAction, onOpenPrivacy } = useDictation((s) => ({
    permissions: s.permissions,
    error: s.error,
    onAction: s.execute,
    onOpenPrivacy: s.privacy,
  }));
  const needsMicrophone = permissions && !permissions.microphone;
  const needsAccessibility = permissions && !permissions.accessibility;
  if (
    !error &&
    state?.phase !== "error" &&
    !needsMicrophone &&
    !needsAccessibility
  )
    return null;
  return (
    <div className="flex flex-col gap-2">
      {(error || state?.phase === "error") && (
        <Alert variant="destructive">
          <TriangleAlert aria-hidden="true" />
          <AlertTitle>Operazione non completata</AlertTitle>
          <AlertDescription className="flex flex-col items-start gap-2">
            <span>
              {error || (state?.message ? toUserMessage(state.message) : null)}
            </span>
            {((state?.phase === "error" && state.modelReady) ||
              needsAccessibility) && (
              <span className="flex flex-wrap gap-2">
                {state?.phase === "error" && state.modelReady && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void onAction("cancel_recording")}
                  >
                    Annulla dettatura
                  </Button>
                )}
                {needsAccessibility && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void onOpenPrivacy("accessibility")}
                  >
                    Apri Accessibilità
                    <ArrowUpRight data-icon="inline-end" />
                  </Button>
                )}
              </span>
            )}
          </AlertDescription>
        </Alert>
      )}
      {needsMicrophone && (
        <Alert>
          <Mic aria-hidden="true" />
          <AlertTitle>Accesso al microfono necessario</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
            <span>Serve per registrare la dettatura.</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                permissions.microphoneStatus === "not-determined"
                  ? void onAction("request_microphone")
                  : void onOpenPrivacy("microphone")
              }
            >
              Consenti microfono
              <ArrowUpRight data-icon="inline-end" />
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {needsAccessibility && (
        <Alert>
          <Keyboard aria-hidden="true" />
          <AlertTitle>Accesso ad Accessibilità necessario</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
            <span>
              Serve per incollare la dettatura al cursore nelle altre app.
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void onOpenPrivacy("accessibility")}
            >
              Consenti Accessibilità
              <ArrowUpRight data-icon="inline-end" />
            </Button>
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}
