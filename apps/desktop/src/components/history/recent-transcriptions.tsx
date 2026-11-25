import { Link } from "@tanstack/react-router";
import { ArrowRight, Copy, History, RotateCcw } from "lucide-react";
import { PageSection } from "@/components/layout/page";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Item, ItemContent, ItemDescription } from "@/components/ui/item";
import { Skeleton } from "@/components/ui/skeleton";
import { useHistory } from "@/hooks/use-history";
import { formatDuration } from "@/lib/history";
import { copyTranscription } from "./actions";
import { dayLabel, formatDateTime, formatTime, isoDate } from "./format";

const RECENT_LIMIT = 5;

/** The last five transcriptions on the home page, from the shared first page. */
export function RecentTranscriptions() {
  const { pages, loading, error, refresh } = useHistory("");
  const entries = pages?.[0]?.entries.slice(0, RECENT_LIMIT);
  return (
    <PageSection
      title="Recenti"
      actions={
        entries && entries.length > 0 ? (
          <Button asChild variant="ghost" size="sm">
            <Link to="/history" aria-label="Vedi tutto lo storico">
              Vedi tutto
              <ArrowRight data-icon="inline-end" />
            </Link>
          </Button>
        ) : null
      }
    >
      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Storico non disponibile</AlertTitle>
          <AlertDescription className="flex flex-col items-start gap-2">
            <span>{error}</span>
            <Button variant="outline" size="sm" onClick={refresh}>
              <RotateCcw data-icon="inline-start" />
              Riprova
            </Button>
          </AlertDescription>
        </Alert>
      ) : !entries ? (
        loading && (
          <div className="flex flex-col gap-1" aria-hidden="true">
            {Array.from({ length: 3 }, (_, index) => (
              <div key={index} className="flex flex-col gap-2 px-3 py-2.5">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-3 w-24" />
              </div>
            ))}
          </div>
        )
      ) : entries.length === 0 ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <History />
            </EmptyMedia>
            <EmptyTitle>Nessuna dettatura recente</EmptyTitle>
            <EmptyDescription>
              Le dettature concluse compaiono qui e nello Storico.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <ul className="flex flex-col" aria-busy={loading}>
          {entries.map((entry) => (
            <li key={entry.id} className="flex min-w-0 items-center gap-1">
              <Item asChild size="sm" className="min-w-0 flex-1">
                <Link
                  to="/history/$transcriptionId"
                  params={{ transcriptionId: entry.id }}
                >
                  <ItemContent className="min-w-0">
                    <p className="truncate text-sm">{entry.text}</p>
                    <ItemDescription>
                      <time dateTime={isoDate(entry.createdAt)}>
                        {dayLabel(entry.createdAt)},{" "}
                        {formatTime(entry.createdAt)}
                      </time>
                      {" · "}
                      {formatDuration(entry.durationSeconds)}
                    </ItemDescription>
                  </ItemContent>
                </Link>
              </Item>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Copia trascrizione del ${formatDateTime(entry.createdAt)}`}
                title="Copia testo"
                onClick={() => void copyTranscription(entry.id)}
              >
                <Copy />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </PageSection>
  );
}
