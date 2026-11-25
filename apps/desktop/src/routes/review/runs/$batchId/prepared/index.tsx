import { createFileRoute, useSearch } from "@tanstack/react-router";
import { analysisSearch } from "@/components/analysis/run-navigation";
import { InvalidRun, RunDetailPage } from "@/components/analysis/run-detail";
import { idParams } from "@/lib/route-params";

export const Route = createFileRoute("/review/runs/$batchId/prepared/")({
  params: idParams("batchId"),
  validateSearch: analysisSearch,
  notFoundComponent: InvalidPrepared,
  component: PreparedPage,
});

function InvalidPrepared() {
  // The match failed on its params, so read the raw search and re-validate it.
  const search = analysisSearch.parse(useSearch({ strict: false }));
  return <InvalidRun search={search} />;
}

function PreparedPage() {
  const { batchId } = Route.useParams();
  const search = Route.useSearch();
  return (
    <RunDetailPage
      key={`${batchId}:prepared`}
      runKey={{ batchId, attemptId: null }}
      search={search}
    />
  );
}
