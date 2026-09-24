import { AudioLines, Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DictationState } from "@/lib/desktop";

const phaseLabels = {
  idle: "Pronto quando vuoi",
  loading: "Preparazione del modello",
  recording: "Ti ascolto",
  paused: "Dettatura in pausa",
  finalizing: "Ultime parole…",
  complete: "Dettatura completata",
  error: "Serve la tua attenzione",
};
const duration = (seconds: number) =>
  `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0")}`;

type TranscriptPanelProps = {
  state: DictationState | null;
  locked: boolean;
  copied: boolean;
  onCopy: () => void;
};

export function TranscriptPanel({
  state,
  locked,
  copied,
  onCopy,
}: TranscriptPanelProps) {
  const recording = state?.phase === "recording";
  const text =
    state?.finalText ||
    [state?.transcript.confirmed, state?.transcript.partial]
      .filter(Boolean)
      .join(" ");
  const words = text?.trim() ? text.trim().split(/\s+/).length : 0;
  const status = state ? phaseLabels[state.phase] : "Collegamento all’app";
  return (
    <section className="dictation-panel" aria-label="Dettatura in tempo reale">
      <div className="panel-toolbar">
        <span className="live-status" data-active={recording}>
          <span />
          {status}
        </span>
        <span className="timer">{duration(state?.elapsedSeconds ?? 0)}</span>
      </div>
      <div
        className="transcript-area"
        role="log"
        aria-label="Trascrizione"
        aria-live="off"
        tabIndex={0}
      >
        {text ? (
          <p className="transcript-text">
            {state?.finalText || (
              <>
                {state?.transcript.confirmed}
                {state?.transcript.confirmed && " "}
                <span className="partial-text">
                  {state?.transcript.partial}
                </span>
              </>
            )}
            {recording && <span className="typing-caret" />}
          </p>
        ) : (
          <div className="transcript-empty">
            <div className="sound-orbit">
              <AudioLines />
            </div>
            <h2>
              {recording ? "Sto ascoltando." : "Da un pensiero a un testo."}
            </h2>
            <p>
              {recording
                ? "Le parole appariranno qui mentre parli."
                : "La trascrizione appare mentre parli.\nQuando termini, VoiceFlow lo incolla nel campo in cui scrivi."}
            </p>
          </div>
        )}
      </div>
      <div className="panel-footer">
        <span>
          {words} parole<span className="footer-divider">·</span>
          {recording
            ? "Trascrizione in corso"
            : "Anteprima live, incolla alla fine"}
        </span>
        <Button
          variant="ghost"
          size="sm"
          disabled={!text || locked}
          onClick={() => void onCopy()}
        >
          {copied ? (
            <Check data-icon="inline-start" />
          ) : (
            <Copy data-icon="inline-start" />
          )}
          {copied ? "Copiato" : "Copia testo"}
        </Button>
      </div>
    </section>
  );
}
