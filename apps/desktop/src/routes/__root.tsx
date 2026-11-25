import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, Outlet } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/app-shell";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { useDictationConnection } from "@/hooks/use-dictation-connection";
import { useQueryEvents } from "@/hooks/use-query-events";

export type RouterContext = { queryClient: QueryClient };

export const Route = createRootRouteWithContext<RouterContext>()({
  component: RootLayout,
});

function RootLayout() {
  useQueryEvents();
  useDictationConnection();
  return (
    <TooltipProvider>
      <AppShell>
        <Outlet />
      </AppShell>
      <Toaster containerAriaLabel="Notifiche" />
    </TooltipProvider>
  );
}
