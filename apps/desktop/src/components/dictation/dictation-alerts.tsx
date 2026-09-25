import { ArrowUpRight, Mic, Keyboard, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import type { DictationState, Permissions, Action } from "@/lib/desktop";

type DictationAlertsProps = {
  state: DictationState | null;
  permissions: Permissions | null;
  error: string | null;
  onAction: (action: Action) => void;
  onOpenPrivacy: (section: "microphone" | "accessibility") => void;
};

export function DictationAlerts({
  state,
  permissions,
  error,
  onAction,
  onOpenPrivacy,
}: DictationAlertsProps) {
  const needsMicrophone = permissions && !permissions.microphone;
  const needsAccessibility = permissions && !permissions.accessibility;
  return (
    <>
      {(error || state?.phase === "error") && (
        <Alert variant="destructive">
          <TriangleAlert aria-hidden="true" />
          <AlertTitle>Operazione non completata</AlertTitle>
          <AlertDescription>{error || state?.message}</AlertDescription>
          {state?.phase === "error" && state.modelReady && (
            <Button
              className="col-start-2 mt-2 justify-self-start"
              variant="outline"
              size="sm"
              onClick={() => void onAction("cancel_recording")}
            >
              Annulla dettatura
            </Button>
          )}
          {needsAccessibility && (
            <Button
              className="col-start-2 mt-2 justify-self-start"
              variant="outline"
              size="sm"
              onClick={() => void onOpenPrivacy("accessibility")}
            >
              Apri Accessibilità
              <ArrowUpRight data-icon="inline-end" />
            </Button>
          )}
        </Alert>
      )}
      {needsMicrophone && (
        <Alert>
          <Mic />
          <AlertTitle>Consenti l’accesso al microfono.</AlertTitle>
          <AlertDescription>
            Consenti l’accesso al microfono per iniziare a dettare.
          </AlertDescription>
          <Button
            className="col-start-2 mt-2 justify-self-start"
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
        </Alert>
      )}
      {needsAccessibility && (
        <Alert>
          <Keyboard />
          <AlertTitle>
            Consenti a VoiceFlow di incollare dove scrivi.
          </AlertTitle>
          <AlertDescription>
            Apri Accessibilità e abilita VoiceFlow. Il permesso serve per
            inserire la dettatura al cursore in T3 Code e nelle altre app.
          </AlertDescription>
          <Button
            className="col-start-2 mt-2 justify-self-start"
            variant="outline"
            size="sm"
            onClick={() => void onOpenPrivacy("accessibility")}
          >
            Consenti Accessibilità
            <ArrowUpRight data-icon="inline-end" />
          </Button>
        </Alert>
      )}
    </>
  );
}
