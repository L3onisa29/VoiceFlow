import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { dictionaryApi, type AutoVariant } from "@/lib/dictionary";
import { invalidateLocalQueries, queryKeys } from "@/lib/query-client";
import { toUserMessage } from "@/lib/errors";

const date = new Intl.DateTimeFormat("it-IT", { dateStyle: "medium" });
const sessions = (n: number) => (n === 1 ? "1 sessione" : `${n} sessioni`);

/** Varianti entrate da sole dall'analisi. La chiave segue la revisione del dizionario. */
export function useAutoVariants(version: number | null) {
  return useQuery({
    queryKey: [...queryKeys.dictionary, "auto", version],
    queryFn: dictionaryApi.autoVariants,
    enabled: version !== null,
    // The night worker adds variants while the app is open.
    staleTime: 0,
  });
}

export function AutoVariants({
  variants,
  disabled,
}: {
  variants: AutoVariant[];
  disabled: boolean;
}) {
  const client = useQueryClient();
  const [removing, setRemoving] = useState<string | null>(null);
  if (variants.length === 0) return null;
  async function remove(spelling: string) {
    setRemoving(spelling);
    try {
      await dictionaryApi.removeAutoVariant(spelling);
      await invalidateLocalQueries(client, [
        queryKeys.dictionary,
        queryKeys.suggestions,
      ]);
      toast.success(`Variante «${spelling}» tolta`);
    } catch (reason) {
      toast.error(toUserMessage(reason));
    } finally {
      setRemoving(null);
    }
  }
  return (
    <Card aria-label="Varianti aggiunte in automatico">
      <CardHeader>
        <CardTitle>
          <h2 className="flex items-center gap-2">
            <Sparkles aria-hidden="true" className="size-4" />
            Varianti aggiunte in automatico
          </h2>
        </CardTitle>
        <CardDescription>
          Grafie che l'analisi ha segnalato sempre con lo stesso termine.
          Togliere una variante vale come veto: non rientrerà più da sola.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <ul className="flex flex-col divide-y">
          {variants.map((variant) => (
            <li
              key={variant.spelling}
              className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
            >
              <div className="flex min-w-0 flex-col gap-1">
                <span className="break-words">
                  «{variant.spelling}» → {variant.term}
                </span>
                <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant="outline">Automatica</Badge>
                  <span>
                    Vista in {sessions(variant.seenSessions)} ·{" "}
                    {variant.changedSessions === null ||
                    variant.replayedSessions === null
                      ? "riprova sullo storico non disponibile"
                      : `avrebbe cambiato ${sessions(variant.changedSessions)} su ${variant.replayedSessions} salvate`}{" "}
                    · aggiunta il {date.format(variant.addedAt)}
                  </span>
                </span>
              </div>
              <Button
                variant="outline"
                size="sm"
                disabled={disabled || removing !== null}
                aria-label={`Togli la variante automatica ${variant.spelling}`}
                onClick={() => void remove(variant.spelling)}
              >
                <Trash2 data-icon="inline-start" />
                Togli
              </Button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
