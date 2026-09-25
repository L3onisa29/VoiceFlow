import { ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";

export function DictationHeader() {
  return (
    <header className="page-header">
      <div>
        <p className="eyebrow">PENSA A VOCE ALTA</p>
        <h1>Fai spazio alle parole.</h1>
        <p>Posiziona il cursore nella tua app. Detta e incolla con ⌥ Spazio.</p>
      </div>
      <Badge variant="outline">
        <ShieldCheck data-icon="inline-start" />
        Sul tuo Mac
      </Badge>
    </header>
  );
}
