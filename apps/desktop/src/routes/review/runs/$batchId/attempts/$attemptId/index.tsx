import { createFileRoute, useSearch } from "@tanstack/react-router";
import { analysisSearch } from "@/components/analysis/run-navigation";
import { InvalidRun, RunDetailPage } from "@/components/analysis/run-detail";
import { idParams } from "@/lib/route-params";

export const Route = createFileRoute(
  "/review/runs/$batchId/attempts/$attemptId/",
)({
  params: idParams("batchId", "attemptId"),
  validateSearch: analysisSearch,
  notFoundComponent: InvalidAttempt,
  component: AttemptPage,
});

function InvalidAttempt() {
  // The match failed on its params, so read the raw search and re-validate it.
  const search = analysisSearch.parse(useSearch({ strict: false }));
  return <InvalidRun search={search} />;
}

function AttemptPage() {
  const { batchId, attemptId } = Route.useParams();
  const search = Route.useSearch();
  return (
    <RunDetailPage
      key={`${batchId}:${attemptId}`}
      runKey={{ batchId, attemptId }}
      search={search}
    />
  );
}
