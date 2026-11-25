import { useRef, type ReactNode } from "react";
import { Copy, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { languageNames, languageShortNames, type Term } from "@/lib/dictionary";

/** Badges shown before the "+N" overflow badge. */
const VISIBLE_GROUPS = 2;

type RowAction = (term: Term, opener: HTMLElement | null) => void;

function Columns({ selection }: { selection: ReactNode }) {
  return (
    <TableHeader>
      <TableRow className="hover:bg-transparent">
        <TableHead className="w-10">{selection}</TableHead>
        <TableHead>Parola</TableHead>
        <TableHead className="w-1/3">Gruppi</TableHead>
        <TableHead className="w-16">Lingua</TableHead>
        <TableHead className="w-12">
          <span className="sr-only">Azioni</span>
        </TableHead>
      </TableRow>
    </TableHeader>
  );
}

export function TermTable({
  terms,
  groupNames,
  conflicts,
  automatic,
  selection,
  locked,
  onSelect,
  onSelectPage,
  onOpen,
  onDuplicate,
  onDelete,
}: {
  /** The terms of the current page. */
  terms: Term[];
  groupNames: Map<number, string>;
  conflicts: Set<number>;
  /** Lowercased variants added automatically by the analysis. */
  automatic: Set<string>;
  selection: Set<number>;
  locked: boolean;
  onSelect: (id: number, selected: boolean) => void;
  onSelectPage: (selected: boolean) => void;
  onOpen: RowAction;
  onDuplicate: RowAction;
  onDelete: RowAction;
}) {
  const selectedOnPage = terms.filter((term) => selection.has(term.id)).length;
  return (
    <div className="overflow-hidden rounded-lg border">
      <Table className="table-fixed">
        <Columns
          selection={
            <Checkbox
              aria-label="Seleziona le parole della pagina"
              checked={
                selectedOnPage === 0
                  ? false
                  : selectedOnPage === terms.length
                    ? true
                    : "indeterminate"
              }
              onCheckedChange={(checked) => onSelectPage(checked === true)}
            />
          }
        />
        <TableBody>
          {terms.map((term) => (
            <TermRow
              key={term.id}
              term={term}
              groupNames={groupNames}
              conflict={conflicts.has(term.id)}
              automatic={automatic}
              selected={selection.has(term.id)}
              locked={locked}
              onSelect={onSelect}
              onOpen={onOpen}
              onDuplicate={onDuplicate}
              onDelete={onDelete}
            />
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function TermRow({
  term,
  groupNames,
  conflict,
  automatic,
  selected,
  locked,
  onSelect,
  onOpen,
  onDuplicate,
  onDelete,
}: {
  term: Term;
  groupNames: Map<number, string>;
  conflict: boolean;
  automatic: Set<string>;
  selected: boolean;
  locked: boolean;
  onSelect: (id: number, selected: boolean) => void;
  onOpen: RowAction;
  onDuplicate: RowAction;
  onDelete: RowAction;
}) {
  const row = useRef<HTMLTableRowElement>(null);
  // A menu item that opens a dialog hands focus to the dialog, not the trigger.
  const opensDialog = useRef(false);
  const run = (action: RowAction) => () => {
    opensDialog.current = true;
    action(term, row.current);
  };
  const groups = term.groupIds
    .map((id) => groupNames.get(id))
    .filter((name): name is string => name !== undefined);
  const hidden = groups.slice(VISIBLE_GROUPS);
  return (
    <TableRow
      ref={row}
      data-term-id={term.id}
      data-state={selected ? "selected" : undefined}
      tabIndex={0}
      className="cursor-pointer outline-none focus-visible:bg-muted/50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
      onClick={() => {
        if (!locked) onOpen(term, row.current);
      }}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget || locked) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(term, row.current);
        }
      }}
    >
      <TableCell onClick={(event) => event.stopPropagation()}>
        <Checkbox
          aria-label={`Seleziona ${term.text}`}
          checked={selected}
          onCheckedChange={(checked) => onSelect(term.id, checked === true)}
        />
      </TableCell>
      <TableCell>
        <div className="flex min-w-0 flex-col">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate font-medium">{term.text}</span>
            {conflict && <Badge variant="destructive">Conflitto</Badge>}
          </div>
          <span className="truncate text-xs text-muted-foreground">
            {term.variants.length
              ? term.variants
                  .map((variant) =>
                    automatic.has(variant.toLowerCase())
                      ? `${variant} (automatica)`
                      : variant,
                  )
                  .join(" · ")
              : "Promemoria · Nessuna variante"}
          </span>
        </div>
      </TableCell>
      <TableCell>
        {groups.length ? (
          <div className="flex min-w-0 items-center gap-1 overflow-hidden">
            {groups.slice(0, VISIBLE_GROUPS).map((name) => (
              <Badge key={name} variant="secondary" className="min-w-0 shrink">
                <span className="truncate">{name}</span>
              </Badge>
            ))}
            {hidden.length > 0 && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge
                    variant="outline"
                    tabIndex={0}
                    aria-label={`Altri gruppi: ${hidden.join(", ")}`}
                    onClick={(event) => event.stopPropagation()}
                  >
                    +{hidden.length}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>{hidden.join(", ")}</TooltipContent>
              </Tooltip>
            )}
          </div>
        ) : (
          <span className="text-muted-foreground">Senza gruppo</span>
        )}
      </TableCell>
      <TableCell
        className="text-muted-foreground"
        title={languageNames[term.language]}
      >
        {languageShortNames[term.language]}
      </TableCell>
      <TableCell onClick={(event) => event.stopPropagation()}>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              disabled={locked}
              aria-label={`Azioni per ${term.text}`}
            >
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            onCloseAutoFocus={(event) => {
              if (opensDialog.current) event.preventDefault();
              opensDialog.current = false;
            }}
          >
            <DropdownMenuGroup>
              <DropdownMenuItem onSelect={run(onOpen)}>
                <Pencil />
                Modifica
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={run(onDuplicate)}>
                <Copy />
                Duplica
              </DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem variant="destructive" onSelect={run(onDelete)}>
                <Trash2 />
                Elimina
              </DropdownMenuItem>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </TableCell>
    </TableRow>
  );
}

/** Placeholder rows while the dictionary opens for the first time. */
export function TermTableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div
      className="overflow-hidden rounded-lg border"
      role="status"
      aria-label="Caricamento dizionario"
    >
      <Table className="table-fixed">
        <Columns selection={null} />
        <TableBody>
          {Array.from({ length: rows }, (_, index) => (
            <TableRow key={index}>
              <TableCell>
                <Skeleton className="size-4" />
              </TableCell>
              <TableCell>
                <div className="flex flex-col gap-1.5">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-3 w-48" />
                </div>
              </TableCell>
              <TableCell>
                <Skeleton className="h-5 w-20" />
              </TableCell>
              <TableCell>
                <Skeleton className="h-4 w-8" />
              </TableCell>
              <TableCell />
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
