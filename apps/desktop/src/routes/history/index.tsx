import { createFileRoute } from "@tanstack/react-router";
import { TranscriptionHistory } from "@/components/history/transcription-history";
import { Page, PageContent, PageHeader } from "@/components/layout/page";
import { useDictation } from "@/hooks/use-dictation";
import { historyFirstPageQuery, warm } from "@/lib/route-loaders";

export const Route = createFileRoute("/history/")({
  loader: ({ context }) =>
    warm(context.queryClient.ensureInfiniteQueryData(historyFirstPageQuery())),
  component: HistoryPage,
});

function HistoryPage() {
  const saveError = useDictation((s) => s.state?.historyError ?? null);
  return (
    <Page>
      <PageHeader
        title="Storico"
        description="Le dettature salvate su questo Mac: solo testo e dati della sessione, nessun audio."
      />
      <PageContent>
        <TranscriptionHistory saveError={saveError} />
      </PageContent>
    </Page>
  );
}
