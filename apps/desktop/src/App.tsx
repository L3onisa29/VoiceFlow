import {
  AudioLines,
  ArrowUpRight,
  Check,
  Copy,
  Cpu,
  Globe2,
  Keyboard,
  LoaderCircle,
  Mic,
  Pause,
  Play,
  ShieldCheck,
  Square,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { useDictation } from "@/hooks/use-dictation";
import { settingsSchema } from "@/lib/desktop";
import "./App.css";

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

export default function App() {
  const {
    state,
    permissions,
    error,
    busy,
    copied,
    execute,
    updateSettings,
    privacy,
  } = useDictation();
  const recording = state?.phase === "recording";
  const paused = state?.phase === "paused";
  const active = recording || paused;
  const processing =
    state?.phase === "loading" || state?.phase === "finalizing";
  const locked = busy || active || processing;
  const text =
    state?.finalText ||
    [state?.transcript.confirmed, state?.transcript.partial]
      .filter(Boolean)
      .join(" ");
  const words = text?.trim() ? text.trim().split(/\s+/).length : 0;
  const needsMicrophone = permissions && !permissions.microphone;
  const needsAccessibility = permissions && !permissions.accessibility;
  const status = state ? phaseLabels[state.phase] : "Collegamento all’app";
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
        <div className="nav-current">
          <Mic aria-hidden="true" />
          <span>Dettatura</span>
          <span className="nav-dot" />
        </div>
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
            {state?.modelReady
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
      <main>
        <header className="page-header">
          <div>
            <p className="eyebrow">PENSA A VOCE ALTA</p>
            <h1>Fai spazio alle parole.</h1>
            <p>
              Posiziona il cursore nella tua app. Detta e incolla con ⌥ Spazio.
            </p>
          </div>
          <div className="privacy-chip">
            <span />
            Sul tuo Mac
          </div>
        </header>
        {(error || state?.phase === "error") && (
          <Alert variant="destructive">
            <AlertTitle>Operazione non completata</AlertTitle>
            <AlertDescription>{error || state?.message}</AlertDescription>
            {state?.phase === "error" && state.modelReady && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => void execute("cancel_recording")}
              >
                Annulla dettatura
              </Button>
            )}
            {needsAccessibility && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => void privacy("accessibility")}
              >
                Apri Accessibilità
                <ArrowUpRight data-icon="inline-end" />
              </Button>
            )}
          </Alert>
        )}
        {needsMicrophone && (
          <Alert className="permission-callout">
            <Mic />
            <AlertTitle>Una volta sola, il permesso di ascoltarti.</AlertTitle>
            <AlertDescription>
              Consenti l’accesso al microfono per iniziare a dettare.
            </AlertDescription>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                permissions.microphoneStatus === "not-determined"
                  ? void execute("request_microphone")
                  : void privacy("microphone")
              }
            >
              Consenti microfono
              <ArrowUpRight data-icon="inline-end" />
            </Button>
          </Alert>
        )}
        {needsAccessibility && (
          <Alert className="permission-callout">
            <Keyboard />
            <AlertTitle>
              Consenti a VoiceFlow di incollare dove scrivi.
            </AlertTitle>
            <AlertDescription>
              Apri Accessibilità e abilita VoiceFlow. Il permesso serve per
              inserire la dettatura al cursore in T3 Code e nelle altre app.
            </AlertDescription>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void privacy("accessibility")}
            >
              Consenti Accessibilità
              <ArrowUpRight data-icon="inline-end" />
            </Button>
          </Alert>
        )}
        <section
          className="dictation-panel"
          aria-label="Dettatura in tempo reale"
        >
          <div className="panel-toolbar">
            <span className="live-status" data-active={recording}>
              <span />
              {status}
            </span>
            <span className="timer">
              {duration(state?.elapsedSeconds ?? 0)}
            </span>
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
              disabled={!text || active || processing || busy}
              onClick={() => void execute("copy_transcript")}
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
        <section
          className="recording-controls"
          aria-label="Controlli registrazione"
        >
          <div className="recording-main">
            <Button
              size="lg"
              disabled={
                busy ||
                processing ||
                (!active && (!state?.modelReady || !permissions?.microphone))
              }
              onClick={() =>
                void execute(active ? "stop_recording" : "toggle_recording")
              }
            >
              {processing ? (
                <LoaderCircle
                  className="animate-spin"
                  data-icon="inline-start"
                />
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
                onClick={() => void execute("pause_recording")}
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
                onClick={() => void execute("cancel_recording")}
              >
                <X data-icon="inline-start" />
                Annulla
              </Button>
            ) : (
              <span className="shortcut-hint">
                oppure <kbd>⌥</kbd>
                <kbd>Spazio</kbd>
              </span>
            )}
          </div>
          <div className="audio-meter" aria-label="Livello microfono">
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
        <Separator />
        <FieldGroup className="settings-grid">
          <Field>
            <FieldLabel htmlFor="language">
              <Globe2 className="size-3.5" />
              Lingua
            </FieldLabel>
            <Select
              disabled={locked || !state}
              value={state?.settings.language ?? "auto"}
              onValueChange={(language) => {
                if (state)
                  void updateSettings(
                    settingsSchema.parse({ ...state.settings, language }),
                  );
              }}
            >
              <SelectTrigger id="language" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value="auto">Rilevamento automatico</SelectItem>
                  <SelectItem value="italian">Italiano</SelectItem>
                  <SelectItem value="english">English</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
            <p className="field-hint">
              Italiano e inglese, anche nella stessa giornata.
            </p>
          </Field>
          <Field>
            <FieldLabel htmlFor="format">Come vuoi il tuo testo</FieldLabel>
            <Select
              disabled={locked || !state}
              value={state?.settings.format ?? "faithful"}
              onValueChange={(format) => {
                if (state)
                  void updateSettings(
                    settingsSchema.parse({ ...state.settings, format }),
                  );
              }}
            >
              <SelectTrigger id="format" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value="faithful">Dettatura fedele</SelectItem>
                  <SelectItem value="spoken-commands">
                    Comandi per paragrafi e liste
                  </SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
            <p className="field-hint">
              {state?.settings.format === "spoken-commands"
                ? "Pronuncia “a capo”, “nuovo paragrafo” o “punto elenco”."
                : "Conserva le tue parole, con punteggiatura automatica."}
            </p>
          </Field>
        </FieldGroup>
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
              onClick={() => void privacy("microphone")}
            >
              Microfono <ArrowUpRight data-icon="inline-end" />
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void privacy("accessibility")}
            >
              Accessibilità <ArrowUpRight data-icon="inline-end" />
            </Button>
          </div>
        </div>
        {state && !state.modelReady && !processing && (
          <div className="accessibility-row">
            <span>
              {state.modelPresent
                ? "Il modello è disponibile sul Mac."
                : "Il modello NVIDIA non è ancora installato su questo Mac."}
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={!state.modelPresent || busy}
              onClick={() => void execute("load_model")}
            >
              Carica modello
            </Button>
          </div>
        )}
        {state?.message && state.phase !== "error" && (
          <p className="outcome-message" role="status">
            {state.message}
          </p>
        )}
      </main>
    </div>
  );
}
