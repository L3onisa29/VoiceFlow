import { createFileRoute, redirect } from "@tanstack/react-router";
import { forwardIdParams } from "@/lib/route-params";

// Legacy URL: transcriptions moved to Storico. The new route validates the id.
export const Route = createFileRoute("/transcriptions/$transcriptionId/")({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: "/history/$transcriptionId",
      params: forwardIdParams(params),
      replace: true,
    });
  },
});
