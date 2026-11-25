import { Link, useRouter } from "@tanstack/react-router";
import type { ComponentProps, MouseEvent } from "react";
import { z } from "zod";
import { runStates, type RunKey } from "@/lib/analysis-runs";
import { optionalIdSearch } from "@/lib/route-params";

// Filters, cursor and selection survive deep links and reloads; invalid values
// are dropped instead of failing the route.
const runFilter = (values: readonly string[]) =>
  z
    .string()
    .refine((value) => values.includes(value))
    .optional()
    .catch(undefined);
export const analysisSearch = z
  .object({
    state: runFilter(Object.keys(runStates)),
    origin: runFilter(["manual", "automatic", "unknown"]),
    beforeBatch: optionalIdSearch,
    beforeAttempt: optionalIdSearch,
    selectedBatch: optionalIdSearch,
    selectedAttempt: optionalIdSearch,
  })
  .transform((search) => ({
    ...search,
    beforeAttempt: search.beforeBatch ? search.beforeAttempt : undefined,
  }));
export type AnalysisSearch = z.output<typeof analysisSearch>;

/**
 * Opens a run detail. From the list it first records the selection in the list
 * URL, so Back returns to the same filters, page and highlighted row.
 */
export function useOpenRun() {
  const router = useRouter();
  return async (runKey: RunKey, search: AnalysisSearch) => {
    const context = runContext(runKey, search);
    if (router.state.location.pathname === "/review/runs")
      await router.navigate({
        to: "/review/runs",
        search: context,
        replace: true,
      });
    if (runKey.attemptId === null)
      await router.navigate({
        to: "/review/runs/$batchId/prepared",
        params: { batchId: runKey.batchId },
        search: context,
      });
    else
      await router.navigate({
        to: "/review/runs/$batchId/attempts/$attemptId",
        params: {
          batchId: runKey.batchId,
          attemptId: runKey.attemptId,
        },
        search: context,
      });
  };
}
const runContext = (runKey: RunKey, search: AnalysisSearch) => ({
  ...search,
  selectedBatch: runKey.batchId,
  selectedAttempt: runKey.attemptId ?? undefined,
});

export function RunLink({
  runKey,
  search,
  children,
  ...anchorProps
}: ComponentProps<"a"> & {
  runKey: RunKey;
  search: AnalysisSearch;
}) {
  const router = useRouter();
  const openRun = useOpenRun();
  const context = runContext(runKey, search);
  async function select(event: MouseEvent<HTMLAnchorElement>) {
    anchorProps.onClick?.(event);
    if (event.defaultPrevented) return;
    if (
      router.state.location.pathname !== "/review/runs" ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    event.preventDefault();
    await openRun(runKey, search);
  }
  return runKey.attemptId === null ? (
    <Link
      {...anchorProps}
      to="/review/runs/$batchId/prepared"
      params={{ batchId: runKey.batchId }}
      search={context}
      onClick={select}
    >
      {children}
    </Link>
  ) : (
    <Link
      {...anchorProps}
      to="/review/runs/$batchId/attempts/$attemptId"
      params={{
        batchId: runKey.batchId,
        attemptId: runKey.attemptId,
      }}
      search={context}
      onClick={select}
    >
      {children}
    </Link>
  );
}
export const runDate = (value: number | null) =>
  value === null
    ? "Non disponibile"
    : new Date(value).toLocaleString("it-IT", {
        dateStyle: "short",
        timeStyle: "short",
      });
export const runCount = (value: number | null) =>
  value === null ? "Non disponibile" : String(value);
export const runOrigin = (value: string | null) =>
  value === "manual"
    ? "Manuale"
    : value === "automatic"
      ? "Programmata"
      : "Non disponibile";
export const runDuration = (value: number | null) =>
  value === null
    ? "Non disponibile"
    : value < 1000
      ? `${value} ms`
      : `${(value / 1000).toLocaleString("it-IT", { maximumFractionDigits: 1 })} s`;
