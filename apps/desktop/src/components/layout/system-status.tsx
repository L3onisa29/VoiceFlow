import { Link } from "@tanstack/react-router";
import { ArrowUpRight } from "lucide-react";
import { cn } from "cn";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { SidebarMenuButton, SidebarMenuItem } from "@/components/ui/sidebar";
import { useDictation, useDictationState } from "@/hooks/use-dictation";

type Tone = "ready" | "pending" | "warning" | "error";
const dot: Record<Tone, string> = {
  ready: "bg-success",
  pending: "bg-muted-foreground",
  warning: "bg-warning",
  error: "bg-destructive",
};

function useSystemStatus() {
  const state = useDictationState("phase", "modelReady", "message");
  const permissions = useDictation((s) => s.permissions);
  const engine =
    state?.phase === "error"
      ? "Errore"
      : state?.phase === "loading"
        ? "Caricamento"
        : state?.modelReady
          ? "Caricato"
          : "Non caricato";
  const permission = (granted: boolean | undefined) =>
    granted === undefined
      ? "Verifica in corso"
      : granted
        ? "Consentito"
        : "Da consentire";
  // The most blocking problem wins the compact label.
  const summary: { tone: Tone; label: string } =
    state?.phase === "error"
      ? { tone: "error", label: "Errore motore" }
      : permissions && !permissions.microphone
        ? {
            tone: "warning",
            label: "Microfono non autorizzato",
          }
        : permissions && !permissions.accessibility
          ? {
              tone: "warning",
              label: "Accessibilità non autorizzata",
            }
          : state?.phase === "loading" || !state
            ? { tone: "pending", label: "Caricamento modello" }
            : !state.modelReady
              ? { tone: "pending", label: "Modello non caricato" }
              : { tone: "ready", label: "Pronto" };
  return {
    summary,
    message: state?.message ?? null,
    rows: [
      { label: "Motore · Nemotron 3.5", value: engine },
      { label: "Microfono", value: permission(permissions?.microphone) },
      { label: "Accessibilità", value: permission(permissions?.accessibility) },
    ],
  };
}

export function SystemStatus() {
  const { summary, message, rows } = useSystemStatus();
  return (
    <SidebarMenuItem>
      <Popover>
        <PopoverTrigger asChild>
          <SidebarMenuButton aria-label={`Stato del sistema: ${summary.label}`}>
            <span
              aria-hidden="true"
              className={cn("size-2 shrink-0 rounded-full", dot[summary.tone])}
            />
            <span>{summary.label}</span>
          </SidebarMenuButton>
        </PopoverTrigger>
        <PopoverContent side="right" align="end" className="w-72">
          <PopoverHeader>
            <PopoverTitle>Stato del sistema</PopoverTitle>
            <PopoverDescription>
              Riconoscimento vocale locale su questo Mac.
            </PopoverDescription>
          </PopoverHeader>
          <Separator />
          <dl className="flex flex-col gap-2">
            {rows.map(({ label, value }) => (
              <div
                key={label}
                className="flex items-center justify-between gap-3"
              >
                <dt className="text-muted-foreground">{label}</dt>
                <dd>
                  <Badge variant="outline">{value}</Badge>
                </dd>
              </div>
            ))}
          </dl>
          {message && (
            <p className="text-xs text-muted-foreground">{message}</p>
          )}
          <Separator />
          <Button asChild variant="ghost" size="sm" className="justify-between">
            <Link to="/settings" search={{ section: "system" }}>
              Apri impostazioni di sistema
              <ArrowUpRight data-icon="inline-end" />
            </Link>
          </Button>
        </PopoverContent>
      </Popover>
    </SidebarMenuItem>
  );
}
