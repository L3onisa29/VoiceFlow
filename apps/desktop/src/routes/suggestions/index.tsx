import { createFileRoute, redirect } from "@tanstack/react-router";
import { z } from "zod";
import { optionalIdSearch } from "@/lib/route-params";

// Legacy URL: proposals moved to Revisione.
export const Route = createFileRoute("/suggestions/")({
  validateSearch: z.object({
    proposal: optionalIdSearch,
    publication: optionalIdSearch,
  }),
  beforeLoad: ({ search }) => {
    throw redirect({ to: "/review", search, replace: true });
  },
});
