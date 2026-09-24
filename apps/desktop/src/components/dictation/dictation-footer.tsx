import { Keyboard, ArrowUpRight } from "lucide-react";
import { Button } from "@/components/ui/button";

type DictationFooterProps = {
  onOpenPrivacy: (section: "microphone" | "accessibility") => void;
};

export function DictationFooter({ onOpenPrivacy }: DictationFooterProps) {
  return (
    <>
      <footer className="bottom-note">
        <Keyboard className="size-4" />
        <p>
          Usa <strong>⌥ Spazio</strong> nell’app in cui scrivi. Ripremilo per
          terminare e incollare.
          <br />
          <span>
            Durante la registrazione, <strong>⌃ Spazio</strong> annulla la
            dettatura.
          </span>
        </p>
      </footer>
      <div className="accessibility-row" aria-label="Permessi macOS">
        <span>Permessi macOS</span>
        <div className="permission-links">
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
