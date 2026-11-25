import { FolderMinus, FolderPlus, Trash2, X } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemTitle,
} from "@/components/ui/item";
import type { Group } from "@/lib/dictionary";

const selected = (count: number) =>
  count === 1 ? "1 selezionata" : `${count} selezionate`;

/** Actions on the selected terms. Each one runs the single-term commands in turn. */
export function BulkActions({
  count,
  groups,
  removable,
  busy,
  onAssign,
  onRemove,
  onDelete,
  onClear,
}: {
  count: number;
  groups: Group[];
  /** Groups that at least one selected term belongs to. */
  removable: Group[];
  busy: boolean;
  onAssign: (group: Group) => void;
  onRemove: (group: Group) => void;
  onDelete: () => void;
  onClear: () => void;
}) {
  return (
    <Item
      variant="muted"
      size="xs"
      role="region"
      aria-label="Azioni sulle parole selezionate"
    >
      <ItemContent>
        <ItemTitle aria-live="polite">{selected(count)}</ItemTitle>
      </ItemContent>
      <ItemActions className="flex-wrap">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" disabled={busy}>
              <FolderPlus data-icon="inline-start" />
              Assegna a gruppo
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="max-w-64">
            <DropdownMenuGroup>
              {groups.map((group) => (
                <DropdownMenuItem
                  key={group.id}
                  onSelect={() => onAssign(group)}
                >
                  <span className="truncate">{group.name}</span>
                </DropdownMenuItem>
              ))}
              {!groups.length && (
                <DropdownMenuItem disabled>Nessun gruppo</DropdownMenuItem>
              )}
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              disabled={busy || !removable.length}
            >
              <FolderMinus data-icon="inline-start" />
              Rimuovi da gruppo
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="max-w-64">
            <DropdownMenuGroup>
              {removable.map((group) => (
                <DropdownMenuItem
                  key={group.id}
                  onSelect={() => onRemove(group)}
                >
                  <span className="truncate">{group.name}</span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
        <Button variant="outline" size="sm" disabled={busy} onClick={onDelete}>
          <Trash2 data-icon="inline-start" />
          Elimina
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          disabled={busy}
          aria-label="Deseleziona tutte"
          onClick={onClear}
        >
          <X />
        </Button>
      </ItemActions>
    </Item>
  );
}

/** Confirms deleting the terms selected when it was opened. */
export function BulkDeleteDialog({
  count,
  busy,
  onConfirm,
  onClose,
  onReturnFocus,
}: {
  count: number;
  busy: boolean;
  onConfirm: () => void;
  onClose: () => void;
  onReturnFocus: () => void;
}) {
  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <AlertDialogContent
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          onReturnFocus();
        }}
      >
        <AlertDialogHeader>
          <AlertDialogTitle>
            {count === 1 ? "Eliminare 1 parola?" : `Eliminare ${count} parole?`}
          </AlertDialogTitle>
          <AlertDialogDescription>
            Le parole selezionate verranno eliminate dal dizionario e da tutti i
            loro gruppi. Le modifiche valgono dalla prossima dettatura.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Annulla</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={busy}
            onClick={(event) => {
              event.preventDefault();
              onConfirm();
            }}
          >
            {busy ? "Eliminazione…" : "Elimina"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
