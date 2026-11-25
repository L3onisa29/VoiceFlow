import { useEffect } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { ArrowUpRight } from "lucide-react";
import { AnalysisSettings } from "@/components/analysis/analysis-settings";
import { SchedulerSettings } from "@/components/analysis/scheduler-settings";
import { DictationAlerts } from "@/components/dictation/dictation-alerts";
import { DictationSettings } from "@/components/dictation/dictation-settings";
import { PageSection } from "@/components/layout/page";
import { SettingsAdvanced } from "@/components/settings/settings-advanced";
import {
  settingsSearchSchema,
  settingsSections,
} from "@/components/settings/settings-navigation";
import { SettingsSection } from "@/components/settings/settings-section";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { Spinner } from "@/components/ui/spinner";
import { useDictation, useDictationState } from "@/hooks/use-dictation";

export const Route = createFileRoute("/settings/")({
  component: SettingsPage,
  validateSearch: settingsSearchSchema,
});

const shortcuts = [
  { keys: ["⌥", "Spazio"], label: "Inizia, poi termina e incolla" },
  { keys: ["⌃", "Spazio"], label: "Annulla la dettatura in corso" },
  {
    keys: ["⇧", "⌥", "Spazio"],
    label: "Termina e copia, senza incollare",
  },
  { keys: ["⌃", "⌥", "V"], label: "Apri Recenti, le ultime trascrizioni" },
] as const;

function SettingsPage() {
  const { section = "dictation" } = Route.useSearch();
  useEffect(() => {
    const selected = settingsSections.find((item) => item.id === section)!;
    document.getElementById("page-content")?.scrollTo({ top: 0 });
    document.getElementById(selected.heading)?.focus({ preventScroll: true });
  }, [section]);
  const state = useDictationState(
    "phase",
    "settings",
    "modelReady",
    "modelPresent",
    "device",
    "message",
  );
  const { permissions, busy, execute, privacy } = useDictation((s) => ({
    permissions: s.permissions,
    busy: s.busy,
    execute: s.execute,
    privacy: s.privacy,
  }));
  const processing =
    state?.phase === "loading" ||
    state?.phase === "stopping" ||
    state?.phase === "finalizing";
  const locked =
    busy ||
    processing ||
    state?.phase === "recording" ||
    state?.phase === "paused";
  return (
    <>
      <div hidden={section !== "dictation"}>
        <SettingsSection
          id="text-settings"
          title="Dettatura"
          description="Lingua e formato del testo. Le modifiche si applicano subito."
        >
          {locked && (
            <Alert>
              <AlertTitle>Preferenze bloccate</AlertTitle>
              <AlertDescription>
                Termina la dettatura o attendi la fine dell'operazione.
              </AlertDescription>
            </Alert>
          )}
          <DictationSettings settings={state?.settings} locked={locked} />
        </SettingsSection>
      </div>
      {/* Keep text being edited while moving between categories. */}
      <div hidden={section !== "analysis"}>
        <AnalysisSettings />
      </div>
      <div hidden={section !== "automation"}>
        <SchedulerSettings />
      </div>
      <div hidden={section !== "system"}>
        <SettingsSection
          id="system-settings"
          title="Sistema"
          description="Motore locale, permessi di macOS e scorciatoie."
        >
          <PageSection title="Motore e permessi">
            <ItemGroup className="gap-2">
              <Item role="listitem" variant="outline" size="sm">
                <ItemContent>
                  <ItemTitle>Motore locale · Nemotron 3.5</ItemTitle>
                  {state && !state.modelReady && !state.modelPresent && (
                    <ItemDescription>
                      Scarica il modello con <code>pnpm model:download</code>{" "}
                      dalla cartella di VoiceFlow, poi riapri l'app.
                    </ItemDescription>
                  )}
                </ItemContent>
                <ItemActions>
                  {!state ? (
                    <Badge variant="outline">Verifica in corso</Badge>
                  ) : state.modelReady ? (
                    <Badge variant="success">Caricato</Badge>
                  ) : state.phase === "loading" ? (
                    <Badge variant="secondary">
                      <Spinner data-icon="inline-start" />
                      Caricamento
                    </Badge>
                  ) : state.modelPresent ? (
                    <>
                      <Badge variant="warning">Non caricato</Badge>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy || processing}
                        onClick={() => void execute("load_model")}
                      >
                        Carica modello
                      </Button>
                    </>
                  ) : (
                    <Badge variant="warning">Da installare</Badge>
                  )}
                </ItemActions>
              </Item>
              {(["microphone", "accessibility"] as const).map((kind) => {
                const name =
                  kind === "microphone" ? "microfono" : "accessibilità";
                const granted = permissions?.[kind];
                return (
                  <Item key={kind} role="listitem" variant="outline" size="sm">
                    <ItemContent>
                      <ItemTitle>
                        {kind === "microphone" ? "Microfono" : "Accessibilità"}
                      </ItemTitle>
                    </ItemContent>
                    <ItemActions>
                      {!permissions ? (
                        <Badge variant="outline">Verifica in corso</Badge>
                      ) : granted ? (
                        <Badge variant="success">Consentito</Badge>
                      ) : (
                        <Badge variant="destructive">Da consentire</Badge>
                      )}
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`Apri permessi ${name}`}
                        onClick={() => void privacy(kind)}
                      >
                        Apri
                        <ArrowUpRight data-icon="inline-end" />
                      </Button>
                    </ItemActions>
                  </Item>
                );
              })}
            </ItemGroup>
            <DictationAlerts />
          </PageSection>
          <PageSection title="Scorciatoie">
            <ItemGroup className="gap-2">
              {shortcuts.map(({ keys, label }) => (
                <Item key={label} role="listitem" variant="outline" size="sm">
                  <ItemContent>
                    <ItemTitle>{label}</ItemTitle>
                  </ItemContent>
                  <ItemActions>
                    <KbdGroup>
                      {keys.map((key) => (
                        <Kbd key={key}>{key}</Kbd>
                      ))}
                    </KbdGroup>
                  </ItemActions>
                </Item>
              ))}
            </ItemGroup>
          </PageSection>
          <SettingsAdvanced label="Avanzate · Diagnostica sistema">
            <div className="flex flex-col gap-2 text-sm text-muted-foreground">
              <p>Ingresso audio: {state?.device ?? "Non rilevato"}</p>
              <p>{state?.message ?? "Nessun messaggio del motore."}</p>
            </div>
          </SettingsAdvanced>
        </SettingsSection>
      </div>
    </>
  );
}
