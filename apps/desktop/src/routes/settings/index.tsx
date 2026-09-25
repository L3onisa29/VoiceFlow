import { AnalysisSettings } from "@/components/analysis/analysis-settings";
import { createRoute } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardAction,
  CardContent,
} from "@/components/ui/card";
import { DictationSettings } from "@/components/dictation/dictation-settings";
import { DictationAlerts } from "@/components/dictation/dictation-alerts";
import { DictationStatus } from "@/components/dictation/dictation-status";
import { DictationFooter } from "@/components/dictation/dictation-footer";
import { useDictation } from "@/contexts/dictation";
import { Route as rootRoute } from "../__root";

export const Route = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  component: SettingsPage,
});

export function SettingsPage() {
  const { state, permissions, error, busy, execute, updateSettings, privacy } =
    useDictation();
  const processing =
    state?.phase === "loading" || state?.phase === "finalizing";
  const locked =
    busy ||
    processing ||
    state?.phase === "recording" ||
    state?.phase === "paused";
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">IL TUO MODO DI DETTARE</p>
          <h1>Impostazioni</h1>
          <p>Lingua, testo e accesso al tuo Mac, in un unico posto.</p>
        </div>
      </header>
      {locked && (
        <Alert>
          <AlertTitle>Impostazioni momentaneamente bloccate</AlertTitle>
          <AlertDescription>
            Termina la dettatura o attendi la fine dell’operazione per
            modificare il testo.
          </AlertDescription>
        </Alert>
      )}
      <Card role="region" aria-labelledby="text-settings">
        <CardHeader>
          <CardTitle>
            <h2 id="text-settings">Lingua e testo</h2>
          </CardTitle>
          <CardDescription>
            Le modifiche si salvano automaticamente sul Mac.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <DictationSettings
            settings={state?.settings}
            locked={locked}
            onChange={updateSettings}
          />
        </CardContent>
      </Card>
      <AnalysisSettings />
      <Card role="region" aria-labelledby="model-settings">
        <CardHeader>
          <CardTitle>
            <h2 id="model-settings">Motore locale</h2>
          </CardTitle>
          <CardDescription>NVIDIA Nemotron 3.5 · Streaming</CardDescription>
          <CardAction>
            <Badge variant="outline">
              {state?.modelReady
                ? "Caricato"
                : state?.phase === "loading"
                  ? "Caricamento"
                  : "Non caricato"}
            </Badge>
          </CardAction>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <DictationStatus
            state={state}
            busy={busy}
            processing={processing}
            onLoadModel={() => void execute("load_model")}
          />
          <p className="text-sm leading-relaxed text-muted-foreground">
            La trascrizione viene elaborata sul tuo Mac. Il modello viene
            caricato all’avvio, quando disponibile.
          </p>
        </CardContent>
      </Card>
      <Card role="region" aria-labelledby="mac-settings">
        <CardHeader>
          <CardTitle>
            <h2 id="mac-settings">Permessi e scorciatoie</h2>
          </CardTitle>
          <CardDescription>
            Detta nell’app in cui stai scrivendo.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">
              Microfono:{" "}
              {permissions
                ? permissions.microphone
                  ? "consentito"
                  : "da consentire"
                : "verifica in corso"}
            </Badge>
            <Badge variant="outline">
              Accessibilità:{" "}
              {permissions
                ? permissions.accessibility
                  ? "consentita"
                  : "da consentire"
                : "verifica in corso"}
            </Badge>
          </div>
          <DictationAlerts
            state={state}
            permissions={permissions}
            error={error}
            onAction={execute}
            onOpenPrivacy={privacy}
          />
          <DictationFooter onOpenPrivacy={privacy} />
        </CardContent>
      </Card>
    </>
  );
}
