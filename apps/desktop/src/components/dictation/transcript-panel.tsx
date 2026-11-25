import { AudioLines, Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Empty,
  EmptyContent,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
  EmptyDescription,
} from "@/components/ui/empty";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useDictation } from "@/hooks/use-dictation";
import type { DictationState } from "@/lib/desktop";
import { selectLocked } from "@/stores/dictation";

const phaseLabels: Record<DictationState["phase"], string> = {
  idle: "In attesa",
  loading: "Preparazione",
  recording: "In ascolto",
  paused: "In pausa",
  stopping: "Chiusura in corso",
  finalizing: "In elaborazione",
  complete: "Completata",
  error: "Interrotta",
};
const phaseVariants: Partial<
  Record<
    DictationState["phase"],
    "default" | "secondary" | "success" | "destructive"
  >
> = {
  recording: "default",
  paused: "secondary",
  complete: "success",
  error: "destructive",
};
const duration = (seconds: number) =>
  `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0")}`;

/** Session phase and elapsed time, shown in the session card header. */
export function SessionPhase() {
  const phase = useDictation((s) => s.state?.phase);
  return (
    <div className="flex items-center gap-2">
      <TranscriptDuration />
      <Badge
        variant={(phase && phaseVariants[phase]) || "outline"}
        role="status"
      >
        {phase ? phaseLabels[phase] : "Connessione…"}
      </Badge>
    </div>
  );
}

function TranscriptDuration() {
  const seconds = useDictation((s) => Math.floor(s.state?.elapsedSeconds ?? 0));
  return (
    <span
      className="text-xs tabular-nums text-muted-foreground"
      aria-label="Durata della dettatura"
    >
      {duration(seconds)}
    </span>
  );
}

/** Live transcript of the current (or last) session, with word count and copy. */
export function TranscriptPanel() {
  const { phase, finalText, confirmed, partial, copied, execute } =
    useDictation((s) => ({
      phase: s.state?.phase,
      finalText: s.state?.finalText,
      confirmed: s.state?.transcript.confirmed,
      partial: s.state?.transcript.partial,
      copied: s.copied,
      execute: s.execute,
    }));
  const locked = useDictation(selectLocked);
  const recording = phase === "recording";
  const listening = recording || phase === "paused";
  const text = finalText || [confirmed, partial].filter(Boolean).join("");
  const onCopy = () => {
    void execute("copy_transcript");
  };
  const words = text?.trim()
    ? text
        .trim()
        .split(/\s+/)
        .filter((word) => /[\p{L}\p{N}]/u.test(word)).length
    : 0;
  return (
    <div className="flex flex-col gap-2">
      <ScrollArea
        className="h-48"
        viewportProps={{
          role: "log",
          "aria-label": "Trascrizione",
          "aria-live": "off",
          tabIndex: 0,
        }}
      >
        {text ? (
          <p className="whitespace-pre-wrap wrap-anywhere pr-4 text-base leading-relaxed">
            {finalText || (
              <>
                {confirmed}
                <span className="text-muted-foreground">{partial}</span>
              </>
            )}
            {recording && (
              <span
                className="ml-0.75 inline-block h-4.75 w-0.5 bg-primary align-middle motion-safe:animate-caret-blink"
                aria-hidden="true"
              />
            )}
          </p>
        ) : (
          <Empty className="min-h-48">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <AudioLines aria-hidden="true" />
              </EmptyMedia>
              <EmptyTitle>
                {listening ? "Nessuna parola ancora" : "Detta in qualsiasi app"}
              </EmptyTitle>
              <EmptyDescription>
                {listening ? (
                  "Le parole compaiono qui mentre parli."
                ) : (
                  "Metti il cursore nel campo in cui scrivi e usa la scorciatoia, oppure il pulsante qui sotto."
                )}
              </EmptyDescription>
            </EmptyHeader>
            {!listening && (
              <EmptyContent>
                <ul
                  aria-label="Altre scorciatoie"
                  className="flex flex-wrap justify-center gap-x-4 gap-y-1 text-xs text-muted-foreground"
                >
                  <li className="flex items-center gap-1.5">
                    <KbdGroup>
                      <Kbd>⇧</Kbd>
                      <Kbd>⌥</Kbd>
                      <Kbd>Spazio</Kbd>
                    </KbdGroup>
                    Termina e copia
                  </li>
                  <li className="flex items-center gap-1.5">
                    <KbdGroup>
                      <Kbd>⌃</Kbd>
                      <Kbd>⌥</Kbd>
                      <Kbd>V</Kbd>
                    </KbdGroup>
                    Recenti
                  </li>
                </ul>
              </EmptyContent>
            )}
          </Empty>
        )}
      </ScrollArea>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="tabular-nums">
          {words === 1 ? "1 parola" : `${words} parole`}
        </span>
        <Button
          variant="ghost"
          size="sm"
          disabled={!text || locked}
          onClick={onCopy}
        >
          {copied ? (
            <Check data-icon="inline-start" />
          ) : (
            <Copy data-icon="inline-start" />
          )}
          {copied ? "Copiato" : "Copia testo"}
        </Button>
      </div>
    </div>
  );
}
