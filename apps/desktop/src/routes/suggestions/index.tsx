import { useCallback, useEffect, useRef, useState } from "react";
import { createRoute } from "@tanstack/react-router";
import {
  Check,
  Clock3,
  Lightbulb,
  Pencil,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Field, FieldLabel } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectGroup,
  SelectItem,
} from "@/components/ui/select";
import {
  Empty,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
  EmptyDescription,
} from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import { CandidateEditor } from "@/components/suggestions/candidate-editor";
import {
  suggestionsApi,
  stateLabels,
  type Proposal,
  type ReviewAction,
  type ReviewRequest,
  type ReviewSnapshot,
} from "@/lib/suggestions";
import { Route as rootRoute } from "../__root";
export const Route = createRoute({
  getParentRoute: () => rootRoute,
  path: "/suggestions",
  component: SuggestionsPage,
});
type DialogState =
  | { kind: "edit" | "reject" | "approve"; proposal: Proposal }
  | { kind: "restore"; version: number };
const languageNames = { "*": "Tutte le lingue", it: "Italiano", en: "Inglese" };

export function SuggestionsPage() {
  const [data, setData] = useState<ReviewSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [filter, setFilter] = useState("review");
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [reason, setReason] = useState("");
  const pending = useRef(false);
  const mounted = useRef(false);
  const retry = useRef<{ identity: string; request: ReviewRequest } | null>(
    null,
  );
  const opener = useRef<HTMLElement | null>(null);
  const refreshButton = useRef<HTMLButtonElement | null>(null);
  const refresh = useCallback(async () => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    try {
      const next = await suggestionsApi.read();
      if (mounted.current) setData(next);
    } catch (error) {
      if (mounted.current) setError(String(error));
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
    };
  }, [refresh]);
  async function act(proposal: Proposal | null, action: ReviewAction) {
    if (!data || pending.current) return;
    const identity = JSON.stringify({
      id: proposal?.id,
      revision: proposal?.revision,
      version: data.version,
      dictionary: data.dictionaryVersion,
      action,
    });
    const request =
      retry.current?.identity === identity
        ? retry.current.request
        : {
            id: proposal?.id ?? null,
            expectedRevision: proposal?.revision ?? null,
            expectedVersion: data.version,
            dictionaryVersion: data.dictionaryVersion,
            requestKey: crypto.randomUUID(),
            action,
          };
    retry.current = { identity, request };
    pending.current = true;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const next = await suggestionsApi.review(request);
      if (mounted.current) {
        setData(next);
        setDialog(null);
        setNotice(
          action.kind === "approve" ||
            action.kind === "disable" ||
            action.kind === "restore"
            ? `Versione ${next.version} pubblicata. Disponibile dalla prossima sessione.`
            : "Revisione salvata. Nessuna nuova regola attivata.",
        );
      }
      retry.current = null;
    } catch (error) {
      if (mounted.current) setError(String(error));
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  function open(next: DialogState) {
    opener.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setReason("");
    setError(null);
    setDialog(next);
  }
  const proposals =
    data?.proposals.filter(
      (p) =>
        filter === "all" ||
        (filter === "review"
          ? p.state === "proposed" || p.state === "deferred"
          : p.state === filter),
    ) ?? [];
  const groupNames = new Map(data?.groups.map((g) => [g.id, g.name]));
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">DECIDI COSA RICORDARE</p>
          <h1>Suggerimenti</h1>
          <p>Valuta ogni proposta prima che cambi la tua dettatura.</p>
        </div>
        <Button
          ref={refreshButton}
          variant="outline"
          disabled={busy}
          onClick={() => void refresh()}
        >
          <RefreshCw data-icon="inline-start" />
          Aggiorna
        </Button>
      </header>
      <Alert>
        <ShieldCheck aria-hidden="true" />
        <AlertDescription>
          Le proposte sono ipotesi. Una correzione ricordata è un esempio
          confermato da te, non autorizza una sostituzione. Solo “Approva e
          pubblica” attiva la regola dalla prossima sessione.
        </AlertDescription>
      </Alert>
      {error && !dialog ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {notice ? (
        <p role="status" className="text-sm text-muted-foreground">
          {notice}
        </p>
      ) : null}
      {!data ? (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              {busy ? <Spinner /> : <Lightbulb />}
            </EmptyMedia>
            <EmptyTitle>
              {busy
                ? "Caricamento suggerimenti…"
                : "Suggerimenti non disponibili"}
            </EmptyTitle>
            <EmptyDescription>
              {busy
                ? "Apertura delle proposte locali."
                : "Riprova con Aggiorna. I dati rimangono sul Mac."}
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <>
          <div className="flex flex-wrap items-end justify-between gap-4">
            <Field className="w-64">
              <FieldLabel htmlFor="review-filter">Mostra</FieldLabel>
              <Select value={filter} onValueChange={setFilter}>
                <SelectTrigger id="review-filter" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectItem value="review">
                      Da valutare e rimandate
                    </SelectItem>
                    <SelectItem value="approved">Approvate e attive</SelectItem>
                    <SelectItem value="rejected">Rifiutate</SelectItem>
                    <SelectItem value="disabled">Disattivate</SelectItem>
                    <SelectItem value="all">Tutte le proposte</SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
            </Field>
            <p className="text-sm text-muted-foreground">
              {proposals.length} proposte · Pubblicazione {data.version} ·
              Dizionario {data.dictionaryVersion}
            </p>
          </div>
          {proposals.length ? (
            proposals.map((proposal) => (
              <Card key={proposal.id} data-proposal-id={proposal.id}>
                <CardHeader>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">
                      {proposal.candidate.definition.category === "terminology"
                        ? "Terminologia"
                        : "Pulizia"}
                    </Badge>
                    <Badge
                      variant={
                        proposal.state === "approved" ? "default" : "secondary"
                      }
                    >
                      {stateLabels[proposal.state]}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      Proposta #{proposal.id}
                    </span>
                  </div>
                  <CardTitle>
                    <h2>
                      {proposal.candidate.definition.category === "terminology"
                        ? proposal.candidate.definition.preferred
                        : proposal.candidate.definition.operation.kind ===
                            "remove-false-start"
                          ? "Falsa partenza"
                          : proposal.candidate.definition.operation.kind ===
                              "repeat"
                            ? "Ripetizione di parola"
                            : proposal.candidate.definition.operation.kind ===
                                "repeat-phrase"
                              ? "Ripetizione di frase"
                              : "Elisione"}
                    </h2>
                  </CardTitle>
                  <CardDescription>{proposal.candidate.reason}</CardDescription>
                </CardHeader>
                <CardContent className="flex flex-col gap-5">
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <div className="min-w-0">
                      <p className="eyebrow">PRIMA</p>
                      <p className="whitespace-pre-wrap break-words">
                        {proposal.candidate.positiveExamples[0].input}
                      </p>
                    </div>
                    <div className="min-w-0">
                      <p className="eyebrow">DOPO PROPOSTO</p>
                      <p className="whitespace-pre-wrap break-words">
                        {proposal.candidate.positiveExamples[0].output}
                      </p>
                    </div>
                  </div>
                  <p className="text-sm text-muted-foreground">
                    {proposal.candidate.definition.category === "terminology"
                      ? `Varianti: ${proposal.candidate.definition.variants.join(", ")}. ${proposal.candidate.definition.matching === "exact" ? "Corrispondenza esatta." : "Maiuscole e minuscole equivalenti."}`
                      : proposal.candidate.definition.operation.kind ===
                          "remove-false-start"
                        ? `Elimina solo la prima parola di “${proposal.candidate.definition.operation.source.join(" ")}”.`
                        : proposal.candidate.definition.operation.kind ===
                            "repeat"
                          ? `Elimina una ripetizione di “${proposal.candidate.definition.operation.word}”.`
                          : proposal.candidate.definition.operation.kind ===
                              "repeat-phrase"
                            ? `Elimina la prima occorrenza di “${proposal.candidate.definition.operation.phrase.join(" ")}” quando ripetuta due volte.`
                            : `Elimina “${proposal.candidate.definition.operation.first}” prima di “${proposal.candidate.definition.operation.prefix}…”.`}
                  </p>
                  <p className="text-sm text-muted-foreground">
                    {languageNames[proposal.candidate.language]} ·{" "}
                    {proposal.candidate.groupIds.length
                      ? proposal.candidate.groupIds
                          .map((id) => groupNames.get(id) ?? "Gruppo rimosso")
                          .join(", ")
                      : "Tutti i contesti"}
                  </p>
                  <p className="text-sm text-muted-foreground">
                    {proposal.candidate.definition.context.before
                      ? `Solo dopo “${proposal.candidate.definition.context.before}”. `
                      : ""}
                    {proposal.candidate.definition.context.after
                      ? `Solo prima di “${proposal.candidate.definition.context.after}”. `
                      : ""}
                    {proposal.candidate.definition.context.exceptBefore.length
                      ? `Esclusa dopo: ${proposal.candidate.definition.context.exceptBefore.join(", ")}. `
                      : ""}
                    {proposal.candidate.definition.context.exceptAfter.length
                      ? `Esclusa prima di: ${proposal.candidate.definition.context.exceptAfter.join(", ")}.`
                      : ""}
                  </p>
                  {proposal.conflicts.length ? (
                    <Alert variant="destructive">
                      <AlertDescription>
                        <p>Pubblicazione bloccata</p>
                        {proposal.conflicts.map((conflict) => (
                          <p key={conflict}>{conflict}</p>
                        ))}
                      </AlertDescription>
                    </Alert>
                  ) : null}
                  {proposal.rejectionReason ? (
                    <p className="text-sm">
                      Motivo del rifiuto precedente: {proposal.rejectionReason}
                    </p>
                  ) : null}
                  <details>
                    <summary className="cursor-pointer text-sm">
                      {proposal.evidence.length}{" "}
                      {proposal.evidence.length === 1
                        ? "riferimento"
                        : "riferimenti"}{" "}
                      ·{" "}
                      {new Set(proposal.evidence.map((e) => e.sessionId)).size}{" "}
                      {new Set(proposal.evidence.map((e) => e.sessionId))
                        .size === 1
                        ? "sessione"
                        : "sessioni"}{" "}
                      ·{" "}
                      {proposal.evidence.filter((e) => e.humanConfirmed).length}{" "}
                      {proposal.evidence.filter((e) => e.humanConfirmed)
                        .length === 1
                        ? "correzione umana confermata"
                        : "correzioni umane confermate"}
                    </summary>
                    <div className="mt-4 flex flex-col gap-4">
                      {proposal.evidence.map((e, index) => (
                        <div
                          key={`${e.sessionId}-${e.revisionId}-${e.originalIndex}`}
                          className="min-w-0 rounded-lg border p-4"
                        >
                          <p className="text-sm">
                            Sessione #{e.sessionId}
                            {e.revisionId
                              ? ` · Revisione #${e.revisionId}`
                              : e.originalIndex !== null
                                ? ` · Originale ASR ${e.originalIndex + 1}`
                                : " · Risultato elaborato"}
                          </p>
                          <Badge variant="outline">
                            {e.humanConfirmed
                              ? "Correzione umana confermata"
                              : e.revisionId
                                ? "Conferma annullata"
                                : "Fonte osservata, ipotesi da valutare"}
                          </Badge>
                          <p
                            aria-label={`Fonte ${index + 1}`}
                            className="mt-3 max-h-48 overflow-y-auto whitespace-pre-wrap break-words text-sm"
                          >
                            {e.before}
                          </p>
                          {e.after ? (
                            <p className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-words text-sm">
                              Correzione: {e.after}
                            </p>
                          ) : null}
                        </div>
                      ))}
                      {!proposal.evidence.length ? (
                        <p className="text-sm text-muted-foreground">
                          Le sessioni sorgenti sono state eliminate. La
                          definizione approvata resta una regola indipendente.
                        </p>
                      ) : null}
                    </div>
                  </details>
                  <details>
                    <summary className="cursor-pointer text-sm">
                      Esempi e frasi di controllo
                    </summary>
                    <div className="mt-3 flex flex-col gap-3">
                      {[
                        ...proposal.candidate.positiveExamples.map((e) => ({
                          ...e,
                          label: "Da correggere",
                        })),
                        ...proposal.candidate.negativeExamples.map((e) => ({
                          ...e,
                          label: "Controllo",
                        })),
                      ].map((e, i) => (
                        <div key={i} className="rounded-lg border p-3 text-sm">
                          <p className="text-muted-foreground">
                            {e.label}. Esempio della definizione, non conteggio
                            di occorrenze.
                          </p>
                          <p className="whitespace-pre-wrap break-words">
                            Prima: {e.input}
                          </p>
                          <p className="whitespace-pre-wrap break-words">
                            Atteso: {e.output}
                          </p>
                        </div>
                      ))}
                    </div>
                  </details>
                </CardContent>
                <CardFooter className="flex flex-wrap gap-2">
                  {proposal.state === "approved" ? (
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() => void act(proposal, { kind: "disable" })}
                    >
                      Disattiva
                    </Button>
                  ) : proposal.state !== "rejected" ? (
                    <Button
                      disabled={busy || proposal.conflicts.length > 0}
                      onClick={() => open({ kind: "approve", proposal })}
                    >
                      <Check data-icon="inline-start" />
                      {proposal.state === "disabled" ? "Riattiva" : "Approva"}
                    </Button>
                  ) : null}
                  {proposal.approvedAt === null ? (
                    <>
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() => open({ kind: "edit", proposal })}
                      >
                        <Pencil data-icon="inline-start" />
                        Modifica
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={
                          busy ||
                          proposal.state === "deferred" ||
                          proposal.state === "rejected"
                        }
                        onClick={() => void act(proposal, { kind: "defer" })}
                      >
                        <Clock3 data-icon="inline-start" />
                        Rimanda
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={busy || proposal.state === "rejected"}
                        onClick={() => open({ kind: "reject", proposal })}
                      >
                        <X data-icon="inline-start" />
                        Rifiuta
                      </Button>
                    </>
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      Approvata il{" "}
                      {new Date(proposal.approvedAt).toLocaleDateString(
                        "it-IT",
                      )}
                    </span>
                  )}
                </CardFooter>
              </Card>
            ))
          ) : (
            <Empty className="border">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <Lightbulb />
                </EmptyMedia>
                <EmptyTitle>Nessuna proposta in questa vista</EmptyTitle>
                <EmptyDescription>
                  I suggerimenti compariranno dopo un’analisi autorizzata. Le
                  correzioni dello storico restano esempi e non generano
                  sostituzioni automatiche.
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}
          <Card>
            <CardHeader>
              <CardTitle>
                <h2>Versioni pubblicate</h2>
              </CardTitle>
              <CardDescription>
                Il ripristino crea una nuova pubblicazione. Le trascrizioni
                passate e il dizionario manuale conservano le proprie versioni.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {data.publications.map((p) => (
                <div
                  key={p.version}
                  className="flex flex-wrap items-center justify-between gap-3"
                >
                  <p className="text-sm">
                    Versione {p.version} · {p.proposalIds.length} regole ·{" "}
                    {new Date(p.createdAt).toLocaleString("it-IT")}
                    {p.version === data.version ? " · Corrente" : ""}
                  </p>
                  {p.version < data.version ? (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() =>
                        open({ kind: "restore", version: p.version })
                      }
                    >
                      <RotateCcw data-icon="inline-start" />
                      Ripristina versione {p.version}
                    </Button>
                  ) : null}
                </div>
              ))}
              {!data.publications.length ? (
                <p className="text-sm text-muted-foreground">
                  Nessuna regola proposta pubblicata. Rimane la pulizia
                  incorporata.
                </p>
              ) : (
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => open({ kind: "restore", version: 0 })}
                >
                  Ripristina solo regole incorporate
                </Button>
              )}
            </CardContent>
            <CardFooter>
              <p className="text-xs text-muted-foreground">
                Tutto rimane sul Mac. Questa pagina non invia testi a fornitori
                esterni.
              </p>
            </CardFooter>
          </Card>
        </>
      )}
      {dialog && data ? (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open && !busy) setDialog(null);
          }}
        >
          <DialogContent
            className="flex max-h-[90dvh] flex-col sm:max-w-2xl"
            showCloseButton={!busy}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              (opener.current?.isConnected
                ? opener.current
                : refreshButton.current
              )?.focus();
            }}
          >
            <DialogHeader>
              <DialogTitle>
                {dialog.kind === "edit"
                  ? "Modifica proposta"
                  : dialog.kind === "reject"
                    ? "Rifiuta proposta"
                    : dialog.kind === "restore"
                      ? `Ripristina versione ${dialog.version}`
                      : "Approva e pubblica"}
              </DialogTitle>
              <DialogDescription>
                {dialog.kind === "edit"
                  ? "Salva la definizione, poi approvala per renderla attiva."
                  : dialog.kind === "reject"
                    ? "Una proposta uguale rimane rifiutata finché non emergono nuove evidenze identificabili."
                    : "La modifica vale dalla prossima sessione. Pausa e ripresa conservano le regole iniziali; lo storico non viene riscritto."}
              </DialogDescription>
            </DialogHeader>
            {error ? (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
            {dialog.kind === "edit" ? (
              <CandidateEditor
                initial={dialog.proposal.candidate}
                groups={data.groups}
                busy={busy}
                onCancel={() => setDialog(null)}
                onSave={(candidate) =>
                  void act(dialog.proposal, { kind: "edit", candidate })
                }
              />
            ) : (
              <>
                {dialog.kind === "reject" ? (
                  <Field>
                    <FieldLabel htmlFor="rejection-reason">
                      Motivo del rifiuto
                    </FieldLabel>
                    <Textarea
                      id="rejection-reason"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      disabled={busy}
                      maxLength={2000}
                    />
                  </Field>
                ) : dialog.kind === "approve" ? (
                  <Alert>
                    <AlertDescription>
                      Approvi una regola indipendente con questi esempi e questa
                      motivazione. Se elimini le sessioni sorgenti, i
                      riferimenti e i loro testi scompaiono; la definizione
                      approvata e le sue versioni restano disponibili per
                      applicazione e ripristino.
                    </AlertDescription>
                  </Alert>
                ) : (
                  <p className="text-sm">
                    La versione scelta sarà ricontrollata insieme al dizionario
                    attuale. Un conflitto blocca il ripristino.
                  </p>
                )}
                <DialogFooter>
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => setDialog(null)}
                  >
                    Annulla
                  </Button>
                  <Button
                    disabled={
                      busy || (dialog.kind === "reject" && !reason.trim())
                    }
                    onClick={() =>
                      void (dialog.kind === "restore"
                        ? act(null, {
                            kind: "restore",
                            version: dialog.version,
                          })
                        : dialog.kind === "reject"
                          ? act(dialog.proposal, { kind: "reject", reason })
                          : act(dialog.proposal, { kind: "approve" }))
                    }
                  >
                    {busy
                      ? "Salvataggio…"
                      : dialog.kind === "reject"
                        ? "Conferma rifiuto"
                        : dialog.kind === "restore"
                          ? "Conferma ripristino"
                          : "Approva e pubblica"}
                  </Button>
                </DialogFooter>
              </>
            )}
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  );
}
