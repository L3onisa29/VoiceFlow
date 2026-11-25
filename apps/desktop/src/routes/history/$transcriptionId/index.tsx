import { createFileRoute, Link, useBlocker } from "@tanstack/react-router";
import { useState } from "react";
import { ArrowLeft } from "lucide-react";
import { HistoryEditor } from "@/components/history/history-editor";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import { idParams } from "@/lib/route-params";

export const Route = createFileRoute("/history/$transcriptionId/")({
  params: idParams("transcriptionId"),
  notFoundComponent: InvalidTranscription,
  component: TranscriptionRoute,
});

function InvalidTranscription() {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyTitle>Trascrizione non trovata</EmptyTitle>
        <EmptyDescription>
          Questo indirizzo non contiene una trascrizione valida.
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button asChild variant="outline" size="sm">
          <Link to="/history">
            <ArrowLeft data-icon="inline-start" />
            Torna allo storico
          </Link>
        </Button>
      </EmptyContent>
    </Empty>
  );
}

function TranscriptionRoute() {
  const { transcriptionId } = Route.useParams();
  return <TranscriptionPage key={transcriptionId} id={transcriptionId} />;
}

function TranscriptionPage({ id }: { id: number }) {
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const blocker = useBlocker({
    shouldBlockFn: () => dirty || busy,
    enableBeforeUnload: dirty || busy,
    withResolver: true,
  });
  return (
    <section
      data-slot="page"
      className="flex flex-col gap-6"
      aria-labelledby="transcription-title"
    >
      <HistoryEditor id={id} onBusy={setBusy} onDirty={setDirty} />
      <AlertDialog
        open={blocker.status === "blocked"}
        onOpenChange={(open) => !open && blocker.reset?.()}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Scartare le modifiche?</AlertDialogTitle>
            <AlertDialogDescription>
              La bozza non è stata salvata. Puoi continuare a modificarla oppure
              scartarla.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Continua a modificare</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={busy}
              onClick={() => blocker.proceed?.()}
            >
              Scarta modifiche
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
