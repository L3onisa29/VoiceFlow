import type { CSSProperties, ReactNode } from "react";
import { Link, useLocation } from "@tanstack/react-router";
import { AudioLines, Cpu } from "lucide-react";
import { cn } from "cn";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
} from "@/components/ui/sidebar";
import { useDictation } from "@/contexts/dictation";
import { primaryNavigation, settingsNavigation } from "./navigation";

function AppSidebar() {
  const { state } = useDictation();
  const pathname = useLocation({ select: (location) => location.pathname });
  const SettingsIcon = settingsNavigation.icon;
  const engineStatus =
    state?.phase === "error"
      ? { color: "bg-destructive", label: "Errore" }
      : state?.phase === "loading"
        ? { color: "bg-warning", label: "Caricamento" }
        : state?.modelReady
          ? { color: "bg-success", label: "Caricato" }
          : { color: "bg-muted-foreground", label: "Da caricare" };
  const sessionLabel =
    state?.phase === "recording"
      ? "Dettatura in corso"
      : state?.phase === "paused"
        ? "Dettatura in pausa"
        : state?.phase === "finalizing"
          ? "Dettatura in elaborazione"
          : null;

  return (
    <Sidebar
      collapsible="none"
      className="shrink-0 border-r border-sidebar-border"
    >
      <SidebarHeader className="py-5">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild tooltip="VoiceFlow">
              <Link to="/" aria-label="VoiceFlow, dettatura">
                <AudioLines aria-hidden="true" />
                <span className="flex min-w-0 flex-col gap-1">
                  <span className="truncate font-semibold">VoiceFlow</span>
                  <span className="truncate text-xs text-muted-foreground">
                    Le parole, al loro posto.
                  </span>
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Il tuo spazio</SidebarGroupLabel>
          <SidebarGroupContent>
            <nav aria-label="Navigazione principale">
              <SidebarMenu className="gap-[2px]">
                {primaryNavigation.map(({ to, label, icon: Icon }) => (
                  <SidebarMenuItem key={to}>
                    <SidebarMenuButton
                      asChild
                      isActive={pathname === to}
                      tooltip={label}
                    >
                      <Link
                        to={to}
                        activeOptions={{ exact: true }}
                        activeProps={{ "aria-current": "page" }}
                      >
                        <Icon aria-hidden="true" />
                        <span>{label}</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </nav>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter className="gap-3 pb-4">
        <SidebarMenu className="gap-[2px]">
          {sessionLabel && (
            <SidebarMenuItem>
              <SidebarMenuButton asChild tooltip={sessionLabel}>
                <Link to="/">
                  <AudioLines aria-hidden="true" />
                  <span>{sessionLabel}</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          )}
          <SidebarMenuItem>
            <SidebarMenuButton
              asChild
              isActive={pathname === settingsNavigation.to}
              tooltip={settingsNavigation.label}
            >
              <Link
                to={settingsNavigation.to}
                activeProps={{ "aria-current": "page" }}
              >
                <SettingsIcon aria-hidden="true" />
                <span>{settingsNavigation.label}</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
        <Card size="sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Cpu className="size-4 shrink-0" aria-hidden="true" />
              <span className="flex-1 truncate">Nemotron 3.5</span>
              <span
                className={cn(
                  "block size-2 shrink-0 rounded-full",
                  engineStatus.color,
                )}
                role="status"
                aria-label={`Stato motore: ${engineStatus.label}`}
              />
            </CardTitle>
          </CardHeader>
        </Card>
      </SidebarFooter>
    </Sidebar>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="h-svh overflow-hidden pt-[38px]">
      <div className="window-drag" data-tauri-drag-region>
        <span>VOICEFLOW</span>
        <span>Il tuo spazio per dettare</span>
      </div>
      <SidebarProvider
        open
        keyboardShortcut={false}
        className="h-full min-h-0"
        style={{ "--sidebar-width": "14rem" } as CSSProperties}
      >
        <AppSidebar />
        <SidebarInset className="min-h-0 min-w-0 overflow-hidden">
          <div
            id="page-content"
            className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-5 py-6 lg:px-9"
          >
            <div className="mx-auto flex w-full max-w-5xl flex-col gap-5">
              {children}
            </div>
          </div>
        </SidebarInset>
      </SidebarProvider>
    </div>
  );
}
