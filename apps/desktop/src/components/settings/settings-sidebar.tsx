import { useState } from "react";
import { Link, useSearch } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInput,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { WindowDragRegion } from "@/components/layout/window-drag-region";
import { settingsSections } from "./settings-navigation";

export function SettingsSidebar() {
  // The root shell can render the new location before its route match is ready.
  const search = useSearch({ from: "/settings/", shouldThrow: false });
  const section = search?.section ?? "dictation";
  const [query, setQuery] = useState("");
  const words = query.toLocaleLowerCase("it-IT").trim().split(/\s+/);
  const results = settingsSections.filter((item) =>
    words.every((word) =>
      `${item.label} ${item.keywords}`
        .toLocaleLowerCase("it-IT")
        .includes(word),
    ),
  );

  return (
    <Sidebar
      collapsible="none"
      className="shrink-0 border-r border-sidebar-border max-sm:h-auto max-sm:max-h-1/2 max-sm:w-full max-sm:border-r-0 max-sm:border-b"
    >
      <SidebarHeader className="gap-3 p-0 pb-2 max-sm:grid max-sm:grid-cols-[1fr_auto] max-sm:items-center max-sm:gap-2 max-sm:pb-1">
        <WindowDragRegion className="max-sm:col-span-full" />
        <SidebarMenu className="px-2">
          <SidebarMenuItem>
            <SidebarMenuButton asChild>
              <Link to="/">
                <ArrowLeft aria-hidden="true" />
                <span>Torna a VoiceFlow</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
        {/* The selected category owns the page h1. */}
        <h2 className="px-4 text-base font-semibold tracking-tight">
          Impostazioni
        </h2>
        <div className="px-2 max-sm:col-span-full">
          <SidebarInput
            type="search"
            aria-label="Cerca impostazioni"
            placeholder="Cerca impostazioni…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") setQuery("");
            }}
          />
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel className="max-sm:hidden">
            {query.trim() ? "Risultati" : "Preferenze"}
          </SidebarGroupLabel>
          <nav aria-label="Sezioni impostazioni">
            <SidebarMenu className="max-sm:grid max-sm:grid-cols-2">
              {results.map(({ id, label, icon: Icon }) => (
                <SidebarMenuItem key={id}>
                  <SidebarMenuButton asChild isActive={section === id}>
                    <Link
                      to="/settings"
                      search={{ section: id }}
                      aria-current={section === id ? "page" : undefined}
                      onClick={() => setQuery("")}
                    >
                      <Icon aria-hidden="true" />
                      <span>{label}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </nav>
          {results.length === 0 && (
            <p
              role="status"
              className="px-2 py-4 text-sm text-muted-foreground"
            >
              Nessuna impostazione trovata.
            </p>
          )}
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  );
}
