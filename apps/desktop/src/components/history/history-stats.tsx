import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useHistory } from "@/hooks/use-history";
import { formatDuration } from "@/lib/history";
import { numberFormat } from "./format";

/** Totals from the first history page, as a compact row of four stats. */
export function HistoryStats() {
  const { pages, loading } = useHistory("");
  const stats = pages?.[0]?.stats;
  const metrics = [
    {
      label: "Parole totali",
      value: stats ? numberFormat.format(stats.totalWords) : null,
    },
    {
      label: "Dettature",
      value: stats ? numberFormat.format(stats.totalSessions) : null,
    },
    {
      label: "Tempo di dettatura",
      value: stats ? formatDuration(stats.totalSeconds) : null,
    },
    {
      label: "Parole al minuto",
      value: stats
        ? stats.wordsPerMinute === null
          ? "Non disponibile"
          : numberFormat.format(stats.wordsPerMinute)
        : null,
    },
  ];
  return (
    <section
      aria-label="Statistiche"
      className="grid grid-cols-2 gap-3 sm:grid-cols-4"
    >
      {metrics.map(({ label, value }) => (
        <Card key={label} size="sm">
          <CardHeader>
            <CardDescription>{label}</CardDescription>
            <CardTitle className="tabular-nums">
              {value === null ? (
                loading ? (
                  <Skeleton className="h-5 w-12" />
                ) : (
                  "Non disponibile"
                )
              ) : (
                value
              )}
            </CardTitle>
          </CardHeader>
        </Card>
      ))}
    </section>
  );
}
