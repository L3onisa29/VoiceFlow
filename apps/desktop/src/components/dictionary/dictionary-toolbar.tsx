import type { RefObject } from "react";
import { FolderCog, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group";
import { Kbd } from "@/components/ui/kbd";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { Dictionary, TermFilter, TermSort } from "@/lib/dictionary";

export function DictionaryToolbar({
  dictionary,
  counts,
  search,
  filter,
  sort,
  found,
  locked,
  searchRef,
  groupsRef,
  onSearch,
  onFilter,
  onSort,
  onManageGroups,
}: {
  dictionary: Dictionary;
  counts: Map<number, number>;
  search: string;
  filter: TermFilter;
  sort: TermSort;
  /** Terms matching search and filter. */
  found: number;
  locked: boolean;
  searchRef: RefObject<HTMLInputElement | null>;
  groupsRef: RefObject<HTMLButtonElement | null>;
  onSearch: (value: string) => void;
  onFilter: (value: TermFilter) => void;
  onSort: (value: TermSort) => void;
  onManageGroups: () => void;
}) {
  const ungrouped = dictionary.terms.filter(
    (term) => !term.groupIds.length,
  ).length;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <InputGroup className="w-full sm:w-64">
        <InputGroupAddon>
          <Search />
        </InputGroupAddon>
        <InputGroupInput
          ref={searchRef}
          id="dictionary-search"
          type="search"
          aria-label="Cerca parole e varianti"
          aria-keyshortcuts="Meta+F"
          placeholder="Cerca parole e varianti…"
          value={search}
          onChange={(event) => onSearch(event.target.value)}
        />
        <InputGroupAddon align="inline-end">
          <Kbd>⌘F</Kbd>
        </InputGroupAddon>
      </InputGroup>
      <Select
        value={filter}
        onValueChange={(value) => onFilter(value as TermFilter)}
      >
        <SelectTrigger
          id="dictionary-filter"
          aria-label="Filtra per gruppo"
          className="w-48"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value="all">
              Tutte le parole ({dictionary.terms.length})
            </SelectItem>
            <SelectItem value="ungrouped">
              Senza gruppo ({ungrouped})
            </SelectItem>
            {dictionary.groups.map((group) => (
              <SelectItem key={group.id} value={String(group.id)}>
                {group.name} ({counts.get(group.id) ?? 0})
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
      <Select value={sort} onValueChange={(value) => onSort(value as TermSort)}>
        <SelectTrigger
          id="dictionary-sort"
          aria-label="Ordina"
          className="w-36"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value="az">Grafia A–Z</SelectItem>
            <SelectItem value="za">Grafia Z–A</SelectItem>
          </SelectGroup>
        </SelectContent>
      </Select>
      <p className="text-sm text-muted-foreground" role="status">
        {found} {found === 1 ? "parola" : "parole"}
        {search.trim() ? (found === 1 ? " trovata" : " trovate") : ""}
      </p>
      <Button
        ref={groupsRef}
        variant="outline"
        className="ml-auto"
        disabled={locked}
        onClick={onManageGroups}
      >
        <FolderCog data-icon="inline-start" />
        Gruppi
      </Button>
    </div>
  );
}
