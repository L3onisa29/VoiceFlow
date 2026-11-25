import { useState, type CSSProperties, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { AudioLines } from "lucide-react";
import { cn } from "cn";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
} from "@/components/ui/sidebar";
import { SettingsSidebar } from "@/components/settings/settings-sidebar";
import { useDictationState } from "@/hooks/use-dictation";
import { useShortcut } from "@/hooks/use-shortcut";
import { queryKeys } from "@/lib/query-client";
import { suggestionsApi } from "@/lib/suggestions";
import { CommandPalette } from "./command-palette";
import {
  isNavigationActive,
  primaryNavigation,
  settingsNavigation,
} from "./navigation";
import { SystemStatus } from "./system-status";
import { WindowDragRegion } from "./window-drag-region";

// Reads the cached suggestions snapshot without fetching it: the count appears
// once the review page has loaded proposals and follows its later updates.
function useReviewCount() {
  const { data } = useQuery({
    queryKey: queryKeys.suggestions,
    queryFn: suggestionsApi.read,
    enabled: false,
    select: (snapshot) =>
      snapshot.proposals.filter(
        (p) => p.state === "proposed" || p.state === "deferred",
      ).length,
  });
  return data ?? 0;
}

function AppSidebar() {
  const state = useDictationState("phase");
  const pathname = useLocation({ select: (location) => location.pathname });
  const reviewCount = useReviewCount();
  const SettingsIcon = settingsNavigation.icon;
  const sessionLabel =
    state?.phase === "recording"
      ? "Dettatura in corso"
      : state?.phase === "paused"
        ? "Dettatura in pausa"
        : state?.phase === "stopping"
          ? "Chiusura registrazione…"
          : state?.phase === "finalizing"
            ? "Dettatura in elaborazione"
            : null;

  return (
    <Sidebar
      collapsible="none"
      className="shrink-0 border-r border-sidebar-border"
    >
      <SidebarHeader className="gap-0 p-0">
        <WindowDragRegion />
        <div
          data-tauri-drag-region
          className="flex items-center gap-2 px-4 pb-3 select-none *:pointer-events-none"
        >
          <AudioLines className="size-4 shrink-0" aria-hidden="true" />
          <span className="truncate text-sm font-semibold">VoiceFlow</span>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <nav aria-label="Navigazione principale">
              <SidebarMenu>
                {primaryNavigation.map(({ to, label, icon: Icon }) => {
                  const active = isNavigationActive(pathname, to);
                  return (
                    <SidebarMenuItem key={to}>
                      <SidebarMenuButton asChild isActive={active}>
                        <Link
                          to={to}
                          aria-current={active ? "page" : undefined}
                        >
                          <Icon aria-hidden="true" />
                          <span>{label}</span>
                        </Link>
                      </SidebarMenuButton>
                      {to === "/review" && reviewCount > 0 && (
                        <SidebarMenuBadge
                          aria-label={
                            reviewCount === 1
                              ? "1 proposta da revisionare"
                              : `${reviewCount} proposte da revisionare`
                          }
                        >
                          {reviewCount}
                        </SidebarMenuBadge>
                      )}
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </nav>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          {sessionLabel && (
            <SidebarMenuItem>
              <SidebarMenuButton asChild>
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
              isActive={isNavigationActive(pathname, settingsNavigation.to)}
            >
              <Link to={settingsNavigation.to}>
                <SettingsIcon aria-hidden="true" />
                <span>{settingsNavigation.label}</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SystemStatus />
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
}

function useAppShortcuts(openPalette: () => void) {
  const navigate = useNavigate();
  const go = (to: (typeof primaryNavigation)[number]["to"]) => () =>
    void navigate({ to });
  useShortcut("mod+1", go(primaryNavigation[0].to));
  useShortcut("mod+2", go(primaryNavigation[1].to));
  useShortcut("mod+3", go(primaryNavigation[2].to));
  useShortcut("mod+4", go(primaryNavigation[3].to));
  useShortcut("mod+5", go(primaryNavigation[4].to));
  useShortcut("mod+,", () => void navigate({ to: settingsNavigation.to }));
  useShortcut("mod+k", openPalette);
}

export function AppShell({ children }: { children: ReactNode }) {
  const isSettings = useLocation({
    select: (location) => location.pathname === "/settings",
  });
  const [palette, setPalette] = useState(false);
  useAppShortcuts(() => setPalette((open) => !open));
  return (
    <div className="h-svh overflow-hidden">
      <SidebarProvider
        open
        keyboardShortcut={false}
        className={cn("h-full min-h-0", isSettings && "max-sm:flex-col")}
        style={{ "--sidebar-width": "14rem" } as CSSProperties}
      >
        {isSettings ? <SettingsSidebar /> : <AppSidebar />}
        <SidebarInset className="min-h-0 min-w-0 overflow-hidden">
          <WindowDragRegion className={cn(isSettings && "max-sm:hidden")} />
          <div
            id="page-content"
            data-scroll-restoration-id="page-content"
            className="flex min-h-0 flex-1 flex-col overflow-y-auto px-5 pt-2 pb-8 lg:px-9"
          >
            <div
              className={cn(
                "mx-auto flex w-full flex-col gap-5",
                isSettings ? "max-w-3xl" : "max-w-5xl",
              )}
            >
              {children}
            </div>
          </div>
        </SidebarInset>
      </SidebarProvider>
      {/* Mounted only while open: the dialog title must not stay in the page. */}
      {palette && <CommandPalette open onOpenChange={setPalette} />}
    </div>
  );
}
