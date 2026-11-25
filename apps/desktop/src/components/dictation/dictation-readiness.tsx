import { Badge } from "@/components/ui/badge";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { useDictation } from "@/hooks/use-dictation";

type Readiness = {
  label: string;
  variant: "success" | "warning" | "destructive" | "secondary";
};

/** System readiness for the page header: the most blocking problem wins. */
function useReadiness(): Readiness {
  const { connected, phase, modelReady, microphone, accessibility } =
    useDictation((s) => ({
      connected: s.state !== null,
      phase: s.state?.phase,
      modelReady: s.state?.modelReady ?? false,
      microphone: s.permissions?.microphone,
      accessibility: s.permissions?.accessibility,
    }));
  if (!connected) return { label: "Connessione…", variant: "secondary" };
  if (phase === "error")
    return { label: "Errore del motore", variant: "destructive" };
  if (microphone === false)
    return { label: "Microfono non autorizzato", variant: "warning" };
  if (accessibility === false)
    return { label: "Accessibilità non autorizzata", variant: "warning" };
  if (phase === "loading")
    return {
      label: modelReady ? "Avvio sessione" : "Caricamento modello",
      variant: "secondary",
    };
  if (!modelReady) return { label: "Modello non caricato", variant: "warning" };
  return { label: "Pronto", variant: "success" };
}

export function DictationReadiness() {
  const { label, variant } = useReadiness();
  return (
    <span className="flex flex-wrap items-center gap-2">
      <Badge variant={variant}>{label}</Badge>
      <span>
        Premi{" "}
        <KbdGroup aria-label="Opzione e Spazio">
          <Kbd>⌥</Kbd>
          <Kbd>Spazio</Kbd>
        </KbdGroup>{" "}
        in qualsiasi app per iniziare e terminare.
      </span>
    </span>
  );
}
