import { Link } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  proposalKind,
  proposalTitle,
  reviewFilters,
  stateLabels,
  type Proposal,
  type ReviewFilter,
} from "@/lib/suggestions";
import { ConfidenceBadge } from "./confidence-badge";

/** Left pane of the review: filter plus dense, fully clickable rows. */
export function ProposalQueue({
  proposals,
  selectedId,
  filter,
  onFilterChange,
  publication,
  children,
}: {
  proposals: Proposal[];
  selectedId: number | null;
  filter: ReviewFilter;
  onFilterChange: (filter: ReviewFilter) => void;
  publication: number | undefined;
  children?: React.ReactNode;
}) {
  return (
    <section
      aria-label="Coda delle proposte"
      className="flex min-w-0 flex-col gap-3"
    >
      <div className="flex items-center justify-between gap-3">
        <Select
          value={filter}
          onValueChange={(value) => onFilterChange(value as ReviewFilter)}
        >
          <SelectTrigger aria-label="Mostra" size="sm" className="min-w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {reviewFilters.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
        <span className="text-xs text-muted-foreground tabular-nums">
          {proposals.length === 1
            ? "1 proposta"
            : `${proposals.length} proposte`}
        </span>
      </div>
      {children}
      {proposals.length > 0 && (
        <ul className="flex flex-col gap-1">
          {proposals.map((proposal) => {
            const selected = proposal.id === selectedId;
            const example = proposal.candidate.positiveExamples[0];
            return (
              <li key={proposal.id}>
                <Item
                  asChild
                  size="sm"
                  variant={selected ? "muted" : "default"}
                >
                  <Link
                    to="/review"
                    search={{ proposal: proposal.id, publication }}
                    replace
                    data-proposal-id={proposal.id}
                    aria-current={selected ? "page" : undefined}
                  >
                    <ItemContent className="min-w-0">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge variant="outline">
                          {proposalKind(proposal.candidate)}
                        </Badge>
                        {proposal.state !== "proposed" && (
                          <Badge variant="secondary">
                            {stateLabels[proposal.state]}
                          </Badge>
                        )}
                      </div>
                      <ItemTitle className="w-full">
                        <span className="truncate">
                          {proposalTitle(proposal.candidate)}
                        </span>
                      </ItemTitle>
                      <ItemDescription className="line-clamp-1">
                        {example.input} → {example.output}
                      </ItemDescription>
                    </ItemContent>
                    <ItemActions className="self-start">
                      <ConfidenceBadge score={proposal.confidence} compact />
                    </ItemActions>
                  </Link>
                </Item>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
