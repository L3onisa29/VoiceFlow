import { useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldLabel } from "@/components/ui/field";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import {
  replayHeadline,
  type Proposal,
  type ReviewAction,
  type ReviewSnapshot,
  type RuleReplay,
} from "@/lib/suggestions";
import { CandidateEditor } from "./candidate-editor";

export type ReviewDialogState =
  | { kind: "edit" | "reject" | "approve"; proposal: Proposal }
  | { kind: "restore"; version: number };

const titles = {
  edit: "Modifica proposta",
  reject: "Rifiuta proposta",
  approve: "Approva e pubblica",
} as const;

/** Edit, reject, approve and restore confirmations. Writes go through `onConfirm`. */
export function ReviewDialog({
  dialog,
  groups,
  replay,
  replaySessions,
  busy,
  error,
  onClose,
  onConfirm,
  returnFocus,
}: {
  dialog: ReviewDialogState;
  groups: ReviewSnapshot["groups"];
  replay: RuleReplay | null;
  replaySessions: number;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: (proposal: Proposal | null, action: ReviewAction) => void;
  returnFocus: () => void;
}) {
  const [reason, setReason] = useState("");
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent
        className="flex max-h-[90dvh] flex-col sm:max-w-2xl"
        showCloseButton={!busy}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          returnFocus();
        }}
      >
        <DialogHeader>
          <DialogTitle>
            {dialog.kind === "restore"
              ? `Ripristina versione ${dialog.version}`
              : titles[dialog.kind]}
          </DialogTitle>
          <DialogDescription>
            {dialog.kind === "edit"
              ? "Salva la definizione, poi approvala per renderla attiva."
              : dialog.kind === "reject"
                ? "Una proposta uguale rimane rifiutata finché non emergono nuove evidenze identificabili."
                : "La modifica vale dalla prossima sessione. Pausa e ripresa conservano le regole iniziali; lo storico non viene riscritto."}
          </DialogDescription>
        </DialogHeader>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {dialog.kind === "edit" ? (
          <CandidateEditor
            initial={dialog.proposal.candidate}
            groups={groups}
            busy={busy}
            onCancel={onClose}
            onSave={(candidate) =>
              onConfirm(dialog.proposal, { kind: "edit", candidate })
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
                  motivazione. Se elimini le sessioni sorgenti, i riferimenti e
                  i loro testi scompaiono; la definizione approvata e le sue
                  versioni restano disponibili per applicazione e ripristino.
                </AlertDescription>
                {replay && (
                  <AlertDescription>
                    {replayHeadline(replay, replaySessions)}
                  </AlertDescription>
                )}
              </Alert>
            ) : (
              <p className="text-sm">
                La versione scelta sarà ricontrollata insieme al dizionario
                attuale. Un conflitto blocca il ripristino.
              </p>
            )}
            <DialogFooter>
              <Button variant="outline" disabled={busy} onClick={onClose}>
                Annulla
              </Button>
              <Button
                disabled={busy || (dialog.kind === "reject" && !reason.trim())}
                onClick={() =>
                  dialog.kind === "restore"
                    ? onConfirm(null, {
                        kind: "restore",
                        version: dialog.version,
                      })
                    : dialog.kind === "reject"
                      ? onConfirm(dialog.proposal, { kind: "reject", reason })
                      : onConfirm(dialog.proposal, { kind: "approve" })
                }
              >
                {busy && (
                  <Spinner data-icon="inline-start" aria-hidden="true" />
                )}
                {dialog.kind === "reject"
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
  );
}
