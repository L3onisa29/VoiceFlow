import {
  Link,
  useRouter,
  type ErrorComponentProps,
} from "@tanstack/react-router";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import { toUserMessage } from "@/lib/errors";

// Router-wide fallbacks, rendered inside the app shell.
export function RoutePending() {
  return (
    <Empty>
      <EmptyHeader>
        <Spinner aria-label="Caricamento pagina" />
      </EmptyHeader>
    </Empty>
  );
}

export function RouteError({ error }: ErrorComponentProps) {
  const router = useRouter();
  return (
    <Empty>
      <EmptyHeader>
        <EmptyTitle>
          <h1>Impossibile aprire la pagina</h1>
        </EmptyTitle>
        <EmptyDescription>{toUserMessage(error)}</EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button variant="outline" onClick={() => void router.invalidate()}>
          <RotateCcw data-icon="inline-start" />
          Riprova
        </Button>
      </EmptyContent>
    </Empty>
  );
}

export function RouteNotFound() {
  return (
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
  );
}
