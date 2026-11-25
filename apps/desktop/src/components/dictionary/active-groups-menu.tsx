import { Layers } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Group } from "@/lib/dictionary";

/**
 * Groups used by the next dictation. Terms without a group always apply; the
 * active session keeps the groups it started with.
 */
export function ActiveGroupsMenu({
  groups,
  selectedIds,
  disabled,
  onChange,
  onManage,
}: {
  groups: Group[];
  selectedIds: number[];
  disabled: boolean;
  onChange: (ids: number[]) => void;
  onManage: () => void;
}) {
  const active = groups.filter((group) => selectedIds.includes(group.id));
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" disabled={disabled}>
          <Layers data-icon="inline-start" />
          Gruppi attivi
          <Badge variant="secondary">{active.length}</Badge>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Per la prossima dettatura</DropdownMenuLabel>
          {groups.map((group) => (
            <DropdownMenuCheckboxItem
              key={group.id}
              id={`active-group-${group.id}`}
              checked={selectedIds.includes(group.id)}
              disabled={disabled}
              // Keep the menu open to toggle several groups in a row.
              onSelect={(event) => event.preventDefault()}
              onCheckedChange={(checked) =>
                onChange(
                  checked
                    ? [...selectedIds, group.id]
                    : selectedIds.filter((id) => id !== group.id),
                )
              }
            >
              <span className="truncate">{group.name}</span>
            </DropdownMenuCheckboxItem>
          ))}
          {!groups.length && (
            <DropdownMenuItem disabled>Nessun gruppo</DropdownMenuItem>
          )}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>
          Le parole senza gruppo valgono sempre.
        </DropdownMenuLabel>
        <DropdownMenuGroup>
          <DropdownMenuItem onSelect={onManage}>
            Gestisci gruppi…
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
