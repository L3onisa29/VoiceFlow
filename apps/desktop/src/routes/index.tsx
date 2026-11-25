import { createFileRoute } from "@tanstack/react-router";
import { DictationAlerts } from "@/components/dictation/dictation-alerts";
import { DictationReadiness } from "@/components/dictation/dictation-readiness";
import { DictationSession } from "@/components/dictation/dictation-session";
import { HistoryStats } from "@/components/history/history-stats";
import { RecentTranscriptions } from "@/components/history/recent-transcriptions";
import { Page, PageContent, PageHeader } from "@/components/layout/page";
import { historyFirstPageQuery, warm } from "@/lib/route-loaders";

export const Route = createFileRoute("/")({
  // Stats and recents read the first history page.
  loader: ({ context }) =>
    warm(context.queryClient.ensureInfiniteQueryData(historyFirstPageQuery())),
  component: DictationPage,
});

// No store subscription here: audio updates must not re-render the page.
function DictationPage() {
  return (
    <Page>
      <PageHeader title="Dettatura" description={<DictationReadiness />} />
      <PageContent>
        <DictationAlerts />
        <DictationSession />
        <HistoryStats />
        <RecentTranscriptions />
      </PageContent>
    </Page>
  );
}
