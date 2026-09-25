import { createRootRoute, Link, Outlet } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/app-shell";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyHeader,
  EmptyTitle,
  EmptyDescription,
  EmptyContent,
} from "@/components/ui/empty";
import { DictationProvider } from "@/contexts/dictation";

export const Route = createRootRoute({
  component: () => (
    <DictationProvider>
      <TooltipProvider>
        <AppShell>
          <Outlet />
        </AppShell>
      </TooltipProvider>
    </DictationProvider>
  ),
  notFoundComponent: () => (
    <Empty>
      <EmptyHeader>
        <EmptyTitle>
          <h1>Pagina non trovata</h1>
        </EmptyTitle>
        <EmptyDescription>Questa pagina non è disponibile.</EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button asChild variant="outline">
          <Link to="/">Torna alla dettatura</Link>
        </Button>
      </EmptyContent>
    </Empty>
  ),
});
