import type { RefObject } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  exclusiveTermCount,
  type Dictionary,
  type Group,
  type GroupInput,
} from "@/lib/dictionary";
import type { DeleteTarget } from "./delete-dialog";

const words = (count: number) => (count === 1 ? "1 parola" : `${count} parole`);

/** Create, rename and delete groups. Activation is chosen in the page header. */
export function GroupManager({
  dictionary,
  counts,
  locked,
  childOpen,
  returnFocus,
  onClose,
  onEdit,
  onDelete,
}: {
  dictionary: Dictionary;
  counts: Map<number, number>;
  locked: boolean;
  /** A group editor or confirmation is open on top: keep the panel open. */
  childOpen: boolean;
  returnFocus: RefObject<HTMLElement | null>;
  onClose: () => void;
  onEdit: (group: GroupInput) => void;
  onDelete: (target: DeleteTarget) => void;
}) {
  const remove = (group: Group) =>
    onDelete({
      kind: "group",
      id: group.id,
      label: group.name,
      termCount: counts.get(group.id) ?? 0,
      globalCount: exclusiveTermCount(dictionary.terms, group.id),
    });
  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open && !childOpen) onClose();
      }}
    >
      <SheetContent
        showCloseButton={false}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          returnFocus.current?.focus();
        }}
      >
        <SheetHeader>
          <SheetTitle>Gestisci gruppi</SheetTitle>
          <SheetDescription>
            Crea, rinomina o elimina gruppi. L'attivazione per la dettatura si
            sceglie da Gruppi attivi.
          </SheetDescription>
        </SheetHeader>
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4">
          <Button
            variant="outline"
            disabled={locked}
            onClick={() => onEdit({ id: null, name: "" })}
          >
            <Plus data-icon="inline-start" />
            Crea gruppo
          </Button>
          {dictionary.groups.length ? (
            <ItemGroup className="gap-2">
              {dictionary.groups.map((group) => (
                <Item
                  key={group.id}
                  role="listitem"
                  variant="outline"
                  size="sm"
                >
                  <ItemContent className="min-w-0">
                    <ItemTitle>{group.name}</ItemTitle>
                    <ItemDescription>
                      {words(counts.get(group.id) ?? 0)}
                      {dictionary.selectedGroupIds.includes(group.id) &&
                        " · attivo"}
                    </ItemDescription>
                  </ItemContent>
                  <ItemActions>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      disabled={locked}
                      aria-label={`Rinomina gruppo ${group.name}`}
                      onClick={() => onEdit(group)}
                    >
                      <Pencil />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      disabled={locked}
                      aria-label={`Elimina gruppo ${group.name}`}
                      onClick={() => remove(group)}
                    >
                      <Trash2 />
                    </Button>
                  </ItemActions>
                </Item>
              ))}
            </ItemGroup>
          ) : (
            <Empty className="border">
              <EmptyHeader>
                <EmptyTitle>Nessun gruppo</EmptyTitle>
                <EmptyDescription>
                  Le parole sono disponibili in ogni dettatura. Crea un gruppo
                  per limitarne alcune a un contesto.
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}
        </div>
        <SheetFooter>
          <SheetClose asChild>
            <Button variant="outline">Chiudi</Button>
          </SheetClose>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
