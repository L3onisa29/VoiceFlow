import { createRoute } from "@tanstack/react-router";
import { DictationHeader } from "@/components/dictation/dictation-header";
import { DictationAlerts } from "@/components/dictation/dictation-alerts";
import { TranscriptPanel } from "@/components/dictation/transcript-panel";
import { RecordingControls } from "@/components/dictation/recording-controls";
import { DictationStatus } from "@/components/dictation/dictation-status";
import { TranscriptionHistory } from "@/components/history/transcription-history";
import { useDictation } from "@/contexts/dictation";
import { Route as rootRoute } from "./__root";

export const Route = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: DictationPage,
});

export function DictationPage() {
  const { state, permissions, error, busy, copied, execute, privacy } =
    useDictation();
  const active = state?.phase === "recording" || state?.phase === "paused";
  const processing =
    state?.phase === "loading" || state?.phase === "finalizing";
  const locked = busy || active || processing;

  return (
    <>
      <DictationHeader />
      <DictationAlerts
        state={state}
        permissions={permissions}
        error={error}
        onAction={execute}
        onOpenPrivacy={privacy}
      />
      <TranscriptPanel
        state={state}
        locked={locked}
        copied={copied}
        onCopy={() => void execute("copy_transcript")}
      />
      <RecordingControls
        state={state}
        permissions={permissions}
        busy={busy}
        onAction={execute}
      />
      <DictationStatus
        state={state}
        busy={busy}
        processing={processing}
        onLoadModel={() => void execute("load_model")}
      />
      <TranscriptionHistory saveError={state?.historyError ?? null} />
    </>
  );
}
