import { createFileRoute, redirect } from "@tanstack/react-router";
import { analysisSearch } from "@/components/analysis/run-navigation";

// Legacy URL: the analysis list moved to Revisione › Esecuzioni.
export const Route = createFileRoute("/analysis/")({
  validateSearch: analysisSearch,
  beforeLoad: ({ search }) => {
    throw redirect({ to: "/review/runs", search, replace: true });
  },
});
