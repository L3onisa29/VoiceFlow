import { AudioLines, Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardHeader,
  CardTitle,
  CardAction,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import {
  Empty,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
  EmptyDescription,
} from "@/components/ui/empty";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
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
      .join("");
  const words = text?.trim()
    ? text
        .trim()
        .split(/\s+/)
        .filter((word) => /[\p{L}\p{N}]/u.test(word)).length
    : 0;
  const status = state ? phaseLabels[state.phase] : "Collegamento all’app";
  return (
    <Card role="region" aria-label="Dettatura in tempo reale">
      <CardHeader>
        <CardTitle>
          <h2>Trascrizione</h2>
        </CardTitle>
        <CardAction>
          <Badge variant={recording ? "default" : "outline"} role="status">
            {status}
          </Badge>
        </CardAction>
        <p
          className="text-xs tabular-nums text-muted-foreground"
          aria-label="Durata della dettatura"
        >
          {duration(state?.elapsedSeconds ?? 0)}
        </p>
      </CardHeader>
      <Separator />
      <CardContent>
        <ScrollArea
          className="h-64"
          viewportProps={{
            role: "log",
            "aria-label": "Trascrizione",
            "aria-live": "off",
            tabIndex: 0,
          }}
        >
          {text ? (
            <p className="whitespace-pre-wrap wrap-anywhere pr-4 text-base leading-relaxed">
              {state?.finalText || (
                <>
                  {state?.transcript.confirmed}
                  <span className="text-accent-foreground">
                    {state?.transcript.partial}
                  </span>
                </>
              )}
              {recording && (
                <span className="typing-caret" aria-hidden="true" />
              )}
            </p>
          ) : (
            <Empty className="min-h-64">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <AudioLines aria-hidden="true" />
                </EmptyMedia>
                <EmptyTitle>
                  {recording ? "Sto ascoltando." : "Da un pensiero a un testo."}
                </EmptyTitle>
                <EmptyDescription>
                  {recording
                    ? "Le parole appariranno qui mentre parli."
                    : "La trascrizione appare mentre parli. Quando termini, VoiceFlow lo incolla nel campo in cui scrivi."}
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}
        </ScrollArea>
      </CardContent>
      <CardFooter className="flex-wrap justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>{words} parole</span>
          <Separator orientation="vertical" className="h-3" />
          <span>
            {recording
              ? "Trascrizione in corso"
              : "Anteprima live, incolla alla fine"}
          </span>
        </div>
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
      </CardFooter>
    </Card>
  );
}
