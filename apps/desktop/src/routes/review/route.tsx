import {
  createFileRoute,
  Link,
  Outlet,
  useMatchRoute,
} from "@tanstack/react-router";
import { Page, PageContent, PageHeader } from "@/components/layout/page";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ManualAnalysisButton,
  ManualAnalysisStatus,
  useManualAnalysis,
} from "@/components/analysis/manual-analysis";

export const Route = createFileRoute("/review")({
  component: ReviewLayout,
});

function ReviewLayout() {
  const matchRoute = useMatchRoute();
  const manual = useManualAnalysis();
  // Run details stay under the Esecuzioni tab.
  const tab = matchRoute({ to: "/review/runs", fuzzy: true })
    ? "runs"
    : "proposals";
  return (
    <Page>
      <PageHeader
        title="Revisione"
        description="Proposte da valutare ed esecuzioni dell'analisi."
        actions={<ManualAnalysisButton manual={manual} />}
      >
        <Tabs value={tab}>
          <TabsList variant="line" aria-label="Sezioni revisione">
            <TabsTrigger value="proposals" asChild>
              <Link to="/review" activeOptions={{ exact: true }}>
                Proposte
              </Link>
            </TabsTrigger>
            <TabsTrigger value="runs" asChild>
              <Link to="/review/runs">Esecuzioni</Link>
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </PageHeader>
      <PageContent>
        <ManualAnalysisStatus manual={manual} />
        <Outlet />
      </PageContent>
    </Page>
  );
}
