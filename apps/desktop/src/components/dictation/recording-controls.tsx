import { Square, Mic, Pause, Play, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { Spinner } from "@/components/ui/spinner";
import type { DictationState, Permissions, Action } from "@/lib/desktop";

type RecordingControlsProps = {
  state: DictationState | null;
  permissions: Permissions | null;
  busy: boolean;
  onAction: (action: Action) => void;
};

export function RecordingControls({
  state,
  permissions,
  busy,
  onAction,
}: RecordingControlsProps) {
  const recording = state?.phase === "recording";
  const paused = state?.phase === "paused";
  const active = recording || paused;
  const processing =
    state?.phase === "loading" || state?.phase === "finalizing";
  return (
    <section
      className="flex flex-wrap items-center justify-between gap-4"
      aria-label="Controlli registrazione"
    >
      <div className="flex flex-wrap items-center gap-3">
        <Button
          size="lg"
          disabled={
            busy ||
            processing ||
            (!active && (!state?.modelReady || !permissions?.microphone))
          }
          onClick={() =>
            void onAction(active ? "stop_recording" : "toggle_recording")
          }
        >
          {processing ? (
            <Spinner aria-label="Elaborazione" data-icon="inline-start" />
          ) : active ? (
            <Square data-icon="inline-start" />
          ) : (
            <Mic data-icon="inline-start" />
          )}
          {active
            ? "Termina e incolla"
            : processing
              ? "Un momento…"
              : "Inizia a dettare"}
        </Button>
        {active && (
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void onAction("pause_recording")}
          >
            {paused ? (
              <Play data-icon="inline-start" />
            ) : (
              <Pause data-icon="inline-start" />
            )}
            {paused ? "Riprendi" : "Pausa"}
          </Button>
        )}
        {active ? (
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => void onAction("cancel_recording")}
          >
            <X data-icon="inline-start" />
            Annulla
          </Button>
        ) : (
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            oppure{" "}
            <KbdGroup aria-label="Opzione e Spazio">
              <Kbd>⌥</Kbd>
              <Kbd>Spazio</Kbd>
            </KbdGroup>
          </span>
        )}
      </div>
      <div
        className="audio-meter"
        role="meter"
        aria-label="Livello microfono"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={
          recording
            ? Math.round(Math.min(1, (state?.level ?? 0) * 12) * 100)
            : 0
        }
      >
        {Array.from({ length: 17 }, (_, i) => (
          <span
            key={i}
            style={{
              height: `${recording ? 4 + Math.min(1, (state?.level ?? 0) * 12) * (10 + 18 * Math.abs(Math.sin(i * 1.7))) : 4}px`,
            }}
          />
        ))}
      </div>
    </section>
  );
}
