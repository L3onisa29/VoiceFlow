import { useEffect, useRef } from "react";
import { Link } from "@tanstack/react-router";
import { MoreHorizontalIcon, RotateCcwIcon } from "lucide-react";
import { PageSection } from "@/components/layout/page";
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { ReviewSnapshot } from "@/lib/suggestions";

/** Published rule sets: current version, history and restore. */
export function Publications({
  snapshot,
  linked,
  busy,
  onRestore,
}: {
  snapshot: ReviewSnapshot;
  linked: number | undefined;
  busy: boolean;
  onRestore: (version: number) => void;
}) {
  const section = useRef<HTMLElement | null>(null);
  const missing =
    linked !== undefined &&
    !snapshot.publications.some((p) => p.version === linked);
  const rows = snapshot.publications.filter(
    (p) => linked === undefined || p.version === linked,
  );
  // A deep link to a historical publication lands on this section.
  useEffect(() => {
    if (linked !== undefined)
      section.current?.scrollIntoView({ block: "start" });
  }, [linked]);
  return (
    <PageSection
      ref={section}
      title="Versioni pubblicate"
      description={`Versione corrente ${snapshot.version} · dizionario ${snapshot.dictionaryVersion}. Il ripristino crea una nuova pubblicazione; trascrizioni passate e dizionario manuale non cambiano.`}
      actions={
        snapshot.publications.length > 0 && (
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => onRestore(0)}
          >
            Ripristina solo regole incorporate
          </Button>
        )
      }
    >
      {linked !== undefined && (
        <Alert>
          <AlertDescription>
            Pubblicazione storica #{linked}.{" "}
            {missing
              ? "Versione non più disponibile."
              : "Lo stato corrente delle regole può essere cambiato."}
          </AlertDescription>
          <AlertAction>
            <Button asChild variant="outline" size="xs">
              <Link to="/review" search={{}}>
                Tutte le versioni
              </Link>
            </Button>
          </AlertAction>
        </Alert>
      )}
      {snapshot.publications.length === 0 ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyTitle>Nessuna regola pubblicata</EmptyTitle>
            <EmptyDescription>
              Rimane attiva la pulizia incorporata.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        rows.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Versione</TableHead>
                <TableHead>Regole</TableHead>
                <TableHead>Pubblicata</TableHead>
                <TableHead>
                  <span className="sr-only">Azioni</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((p) => (
                <TableRow
                  key={p.version}
                  data-publication-version={p.version}
                  data-state={p.version === linked ? "selected" : undefined}
                >
                  <TableCell>
                    <span className="flex items-center gap-2">
                      Versione {p.version}
                      {p.version === snapshot.version && (
                        <Badge variant="secondary">Corrente</Badge>
                      )}
                    </span>
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {p.proposalIds.length === 1
                      ? "1 regola"
                      : `${p.proposalIds.length} regole`}
                  </TableCell>
                  <TableCell>
                    {new Date(p.createdAt).toLocaleString("it-IT", {
                      dateStyle: "short",
                      timeStyle: "short",
                    })}
                  </TableCell>
                  <TableCell className="text-right">
                    {p.version < snapshot.version && (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            disabled={busy}
                            aria-label={`Azioni versione ${p.version}`}
                          >
                            <MoreHorizontalIcon />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuGroup>
                            <DropdownMenuItem
                              onSelect={() => onRestore(p.version)}
                            >
                              <RotateCcwIcon />
                              Ripristina versione {p.version}
                            </DropdownMenuItem>
                          </DropdownMenuGroup>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )
      )}
    </PageSection>
  );
}
