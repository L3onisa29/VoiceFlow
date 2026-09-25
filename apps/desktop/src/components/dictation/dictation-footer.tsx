import { Keyboard, ArrowUpRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { Separator } from "@/components/ui/separator";

type DictationFooterProps = {
  onOpenPrivacy: (section: "microphone" | "accessibility") => void;
};

export function DictationFooter({ onOpenPrivacy }: DictationFooterProps) {
  return (
    <>
      <Alert role="note">
        <Keyboard aria-hidden="true" />
        <AlertTitle>Detta nell’app in cui scrivi</AlertTitle>
        <AlertDescription className="flex flex-col gap-2">
          <span>
            Usa{" "}
            <KbdGroup>
              <Kbd>⌥</Kbd>
              <Kbd>Spazio</Kbd>
            </KbdGroup>{" "}
            per iniziare. Ripremilo per terminare e incollare.
          </span>
          <span>
            Durante la registrazione,{" "}
            <KbdGroup>
              <Kbd>⌃</Kbd>
              <Kbd>Spazio</Kbd>
            </KbdGroup>{" "}
            annulla la dettatura.
          </span>
        </AlertDescription>
      </Alert>
      <Separator />
      <div
        className="flex flex-wrap items-center justify-between gap-2"
        aria-label="Permessi macOS"
      >
        <span className="text-sm text-muted-foreground">Permessi macOS</span>
        <div className="flex flex-wrap gap-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void onOpenPrivacy("microphone")}
          >
            Microfono <ArrowUpRight data-icon="inline-end" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void onOpenPrivacy("accessibility")}
          >
            Accessibilità <ArrowUpRight data-icon="inline-end" />
          </Button>
        </div>
      </div>
    </>
  );
}
