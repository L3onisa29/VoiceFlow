import { createRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/app-shell";
import { DictationHeader } from "@/components/dictation/dictation-header";
import { DictationAlerts } from "@/components/dictation/dictation-alerts";
import { TranscriptPanel } from "@/components/dictation/transcript-panel";
import { RecordingControls } from "@/components/dictation/recording-controls";
import { DictationSettings } from "@/components/dictation/dictation-settings";
import { DictationFooter } from "@/components/dictation/dictation-footer";
import { DictationStatus } from "@/components/dictation/dictation-status";
import { Separator } from "@/components/ui/separator";
import { useDictation } from "@/hooks/use-dictation";
import { Route as rootRoute } from "./__root";

export const Route = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: DictationPage,
});

export function DictationPage() {
  const {
    state,
    permissions,
    error,
    busy,
    copied,
    execute,
    updateSettings,
    privacy,
  } = useDictation();
  const active = state?.phase === "recording" || state?.phase === "paused";
  const processing =
    state?.phase === "loading" || state?.phase === "finalizing";
  const locked = busy || active || processing;

  return (
    <AppShell modelReady={state?.modelReady ?? false} processing={processing}>
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
      <Separator />
      <DictationSettings
        settings={state?.settings}
        locked={locked}
        onChange={updateSettings}
      />
      <DictationFooter onOpenPrivacy={privacy} />
      <DictationStatus
        state={state}
        busy={busy}
        processing={processing}
        onLoadModel={() => void execute("load_model")}
      />
    </AppShell>
  );
}
