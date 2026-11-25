import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DictationStatus } from "./dictation-status";
import { RecordingControls } from "./recording-controls";
import { SessionPhase, TranscriptPanel } from "./transcript-panel";

/**
 * The dictation session in one card. Each part subscribes to its own slice of
 * the store, so audio level updates only re-render the meter.
 */
export function DictationSession() {
  return (
    <Card role="region" aria-label="Dettatura in tempo reale">
      <CardHeader>
        <CardTitle>
          <h2>Sessione</h2>
        </CardTitle>
        <CardDescription>
          Al termine il testo viene incollato al cursore dell’app in primo
          piano.
        </CardDescription>
        <CardAction>
          <SessionPhase />
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <DictationStatus />
        <TranscriptPanel />
      </CardContent>
      <CardFooter>
        <RecordingControls />
      </CardFooter>
    </Card>
  );
}
