import { Square, Mic, Pause, Play, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useDictation, useDictationState } from "@/hooks/use-dictation";

export function RecordingControls() {
  const state = useDictationState("phase", "modelReady");
  const { permissions, busy, onAction } = useDictation((s) => ({
    permissions: s.permissions,
    busy: s.busy,
    onAction: s.execute,
  }));
  const recording = state?.phase === "recording";
  const paused = state?.phase === "paused";
  const active = recording || paused;
  const stopping = state?.phase === "stopping";
  const finalizing = state?.phase === "finalizing";
  const processing = state?.phase === "loading" || stopping || finalizing;
  return (
    <div
      role="group"
      className="flex w-full flex-wrap items-center justify-between gap-4"
      aria-label="Controlli registrazione"
    >
      <div className="flex flex-wrap items-center gap-2">
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
            <Spinner
              aria-label={
                state?.phase === "loading"
                  ? "Preparazione in corso"
                  : stopping
                    ? "Chiusura registrazione"
                    : "Elaborazione"
              }
              data-icon="inline-start"
            />
          ) : active ? (
            <Square data-icon="inline-start" />
          ) : (
            <Mic data-icon="inline-start" />
          )}
          {active
            ? "Termina e incolla"
            : state?.phase === "loading"
              ? "Preparazione…"
              : stopping
                ? "Chiusura registrazione…"
                : processing
                  ? "Elaborazione…"
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
        {active || stopping || finalizing ? (
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => void onAction("cancel_recording")}
          >
            <X data-icon="inline-start" />
            Annulla
          </Button>
        ) : null}
      </div>
      <AudioMeter />
    </div>
  );
}

function AudioMeter() {
  const state = useDictationState("phase", "level");
  const recording = state?.phase === "recording";
  return (
    <div
      className="flex h-8.75 items-center gap-0.75 px-1.25"
      role="meter"
      aria-label="Livello microfono"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={
        recording ? Math.round(Math.min(1, (state?.level ?? 0) * 12) * 100) : 0
      }
    >
      {Array.from({ length: 17 }, (_, i) => (
        <span
          key={i}
          className="block min-h-1 w-0.75 rounded-full bg-primary motion-safe:transition-[height] motion-safe:duration-100"
          style={{
            height: `${recording ? 4 + Math.min(1, (state?.level ?? 0) * 12) * (10 + 18 * Math.abs(Math.sin(i * 1.7))) : 4}px`,
          }}
        />
      ))}
    </div>
  );
}
