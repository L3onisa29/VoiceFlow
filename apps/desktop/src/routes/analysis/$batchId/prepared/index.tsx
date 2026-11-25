import { createFileRoute, redirect } from "@tanstack/react-router";
import { forwardIdParams } from "@/lib/route-params";
import { analysisSearch } from "@/components/analysis/run-navigation";

// Legacy URL. Ids are forwarded as-is: the new route validates them.
export const Route = createFileRoute("/analysis/$batchId/prepared/")({
  validateSearch: analysisSearch,
  beforeLoad: ({ params, search }) => {
    throw redirect({
      to: "/review/runs/$batchId/prepared",
      params: forwardIdParams(params),
      search,
      replace: true,
    });
  },
});
