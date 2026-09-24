import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { AudioLines, Mic, ShieldCheck, Cpu } from "lucide-react";
import { Badge } from "@/components/ui/badge";

type AppShellProps = {
  children: ReactNode;
  modelReady: boolean;
  processing: boolean;
};

export function AppShell({ children, modelReady, processing }: AppShellProps) {
  return (
    <div className="app-shell">
      <div className="window-drag" data-tauri-drag-region>
        <span>VOICEFLOW</span>
        <span>Il tuo spazio per dettare</span>
      </div>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <AudioLines aria-hidden="true" />
          </div>
          <span>
            VoiceFlow
            <span className="brand-caption">Le parole, al loro posto.</span>
          </span>
        </div>
        <div className="sidebar-section-label">IL TUO SPAZIO</div>
        <Link to="/" className="nav-current" activeOptions={{ exact: true }}>
          <Mic aria-hidden="true" />
          <span>Dettatura</span>
          <span className="nav-dot" />
        </Link>
        <div className="sidebar-note">
          <ShieldCheck />
          <p>
            La tua voce resta qui.
            <br />
            <span>Trascrizione sul tuo Mac.</span>
          </p>
        </div>
        <div className="sidebar-bottom">
          <div className="engine-symbol">
            <Cpu />
            <span>
              Motore NVIDIA<span>Nemotron 3.5 · Streaming</span>
            </span>
          </div>
          <Badge variant="outline">
            {modelReady
              ? "Modello caricato"
              : processing
                ? "Caricamento"
                : "Modello da caricare"}
          </Badge>
          <p className="version">
            VoiceFlow 0.2 <span>Anteprima</span>
          </p>
        </div>
      </aside>
      <main>{children}</main>
    </div>
  );
}
