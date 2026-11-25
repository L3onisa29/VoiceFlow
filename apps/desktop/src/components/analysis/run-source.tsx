import { useCallback } from "react";
import { Link } from "@tanstack/react-router";
import { historyApi } from "@/lib/history";
import { analysisApi } from "@/lib/analysis";
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useRunResource } from "./use-run-resource";

const sourceInterval = () => 30000;
export function RunSource({
  sessionId,
  revisionId,
}: {
  sessionId: number;
  revisionId: number | null;
}) {
  const load = useCallback(async () => {
    const detail = await historyApi.detail(sessionId);
    // Recheck after reading, so a source revoked during the read stays hidden.
    if (await analysisApi.excluded(sessionId))
      throw new Error("Fonte esclusa dall'analisi.");
    if (
      revisionId !== null &&
      !detail.revisions.some((r) => r.id === revisionId)
    )
      throw new Error("Correzione non più disponibile.");
    return detail;
  }, [sessionId, revisionId]);
  const { data, error, refresh } = useRunResource(
    ["analysis", "source", sessionId, revisionId],
    load,
    sourceInterval,
    true,
  );
  const revision = data?.revisions.find((r) => r.id === revisionId);
  return (
    <>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
          <AlertAction>
            <Button variant="outline" size="xs" onClick={refresh}>
              Riprova fonte
            </Button>
          </AlertAction>
        </Alert>
      )}
      {!data && !error && <Skeleton className="h-24 w-full" />}
      {data && (
        <div className="flex flex-col gap-4 text-sm">
          {revision ? (
            <>
              <div className="flex flex-col gap-1">
                <h3 className="text-xs font-medium text-muted-foreground">
                  Prima della correzione
                </h3>
                <p className="whitespace-pre-wrap break-words">
                  {revision.beforeText}
                </p>
              </div>
              <div className="flex flex-col gap-1">
                <h3 className="text-xs font-medium text-muted-foreground">
                  Correzione #{revision.id}
                </h3>
                <p className="whitespace-pre-wrap break-words">
                  {revision.text}
                </p>
              </div>
              <p className="text-muted-foreground">
                {revision.reverted
                  ? "Questa revisione è stata annullata."
                  : "Revisione conservata nello storico."}
              </p>
            </>
          ) : (
            <>
              <div className="flex flex-col gap-1">
                <h3 className="text-xs font-medium text-muted-foreground">
                  Originale ASR
                </h3>
                <p className="whitespace-pre-wrap break-words">
                  {data.originalAsr?.join("\n") ?? "Non disponibile"}
                </p>
              </div>
              <div className="flex flex-col gap-1">
                <h3 className="text-xs font-medium text-muted-foreground">
                  Testo elaborato
                </h3>
                <p className="whitespace-pre-wrap break-words">
                  {data.producedText}
                </p>
              </div>
            </>
          )}
          <div className="flex flex-col gap-1">
            <h3 className="text-xs font-medium text-muted-foreground">
              Testo corrente nello storico
            </h3>
            <p className="whitespace-pre-wrap break-words">{data.entry.text}</p>
          </div>
          <Button asChild variant="outline">
            <Link to="/history">Apri Storico per modificare</Link>
          </Button>
        </div>
      )}
    </>
  );
}
