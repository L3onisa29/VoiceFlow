import { InfoIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import {
  confidenceBands,
  confidenceLevel,
  confidenceMeaning,
  confidenceVariant,
} from "@/lib/confidence";

/** Score badge. `compact` shows the number only, for dense lists. */
export function ConfidenceBadge({
  score,
  compact = false,
}: {
  score: number | null;
  compact?: boolean;
}) {
  return (
    <Badge variant={confidenceVariant(score)}>
      {compact ? (
        <>
          <span className="sr-only">Confidenza </span>
          {score ?? "n/d"}
        </>
      ) : score === null ? (
        "Confidenza sconosciuta"
      ) : (
        `Confidenza ${score}/100 · ${confidenceLevel(score)}`
      )}
    </Badge>
  );
}

/** Explains the score once, on hover or focus, instead of on every item. */
export function ConfidenceInfo() {
  return (
    <HoverCard openDelay={150} closeDelay={100}>
      <HoverCardTrigger asChild>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label="Come leggere la confidenza"
        >
          <InfoIcon />
        </Button>
      </HoverCardTrigger>
      <HoverCardContent align="end" className="flex w-72 flex-col gap-2">
        <p>{confidenceMeaning}</p>
        <dl className="grid grid-cols-3 gap-x-3 gap-y-1 text-xs">
          {confidenceBands.map((band) => (
            <div key={band.range} className="col-span-3 grid grid-cols-subgrid">
              <dt className="font-medium">{band.range}</dt>
              <dd className="col-span-2 text-muted-foreground">
                {band.label}. {band.note}
              </dd>
            </div>
          ))}
        </dl>
      </HoverCardContent>
    </HoverCard>
  );
}
