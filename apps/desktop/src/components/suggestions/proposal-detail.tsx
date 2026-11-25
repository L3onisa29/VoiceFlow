import { useId, type ReactNode } from "react";
import {
  CheckIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  Clock3Icon,
  PencilIcon,
  PowerOffIcon,
  XIcon,
} from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Kbd } from "@/components/ui/kbd";
import { Separator } from "@/components/ui/separator";
import { findingOutcomes } from "@/lib/confidence";
import { cn } from "@/lib/utils";
import {
  evidenceSummary,
  languageNames,
  proposalContext,
  proposalKind,
  proposalRule,
  proposalTitle,
  replayHeadline,
  stateLabels,
  usageHeadline,
  type Proposal,
  type RuleReplay,
} from "@/lib/suggestions";
import { ConfidenceBadge, ConfidenceInfo } from "./confidence-badge";
import { TextComparison } from "./text-comparison";

export type ProposalCommand =
  "approve" | "edit" | "defer" | "reject" | "disable";

/** Which commands the current state allows. Shared by buttons and shortcuts. */
export function availableCommands(proposal: Proposal) {
  const open = proposal.approvedAt === null;
  return {
    approve:
      proposal.state !== "approved" &&
      proposal.state !== "rejected" &&
      proposal.conflicts.length === 0,
    showApprove: proposal.state !== "approved" && proposal.state !== "rejected",
    disable: proposal.state === "approved",
    edit: open,
    defer:
      open && proposal.state !== "deferred" && proposal.state !== "rejected",
    reject: open && proposal.state !== "rejected",
  };
}

export function ProposalDetail({
  proposal,
  groupNames,
  replay,
  replaySessions,
  replayPending,
  busy,
  position,
  onCommand,
  onStep,
}: {
  proposal: Proposal;
  groupNames: Map<number, string>;
  replay: RuleReplay | null;
  replaySessions: number;
  replayPending: boolean;
  busy: boolean;
  position: { index: number; total: number } | null;
  onCommand: (command: ProposalCommand) => void;
  onStep: (offset: 1 | -1) => void;
}) {
  const titleId = useId();
  const { candidate } = proposal;
  const can = availableCommands(proposal);
  const context = proposalContext(candidate);
  return (
    <article
      aria-labelledby={titleId}
      data-proposal-detail={proposal.id}
      className="flex min-w-0 flex-col gap-5"
    >
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant="outline">{proposalKind(candidate)}</Badge>
            <Badge
              variant={proposal.state === "approved" ? "default" : "secondary"}
            >
              {stateLabels[proposal.state]}
            </Badge>
            <span className="text-xs text-muted-foreground">
              Proposta #{proposal.id}
            </span>
          </div>
          <h2 id={titleId} className="text-lg font-semibold break-words">
            {proposalTitle(candidate)}
          </h2>
          <p className="text-sm text-muted-foreground">
            {proposalRule(candidate)}
          </p>
        </div>
        {position && (
          <div className="flex shrink-0 items-center gap-1">
            <span className="text-xs text-muted-foreground tabular-nums">
              {position.index + 1} di {position.total}
            </span>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Proposta precedente"
              aria-keyshortcuts="K ArrowUp"
              disabled={position.index === 0}
              onClick={() => onStep(-1)}
            >
              <ChevronUpIcon />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Proposta successiva"
              aria-keyshortcuts="J ArrowDown"
              disabled={position.index >= position.total - 1}
              onClick={() => onStep(1)}
            >
              <ChevronDownIcon />
            </Button>
          </div>
        )}
      </header>

      <TextComparison
        before={candidate.positiveExamples[0].input}
        after={candidate.positiveExamples[0].output}
        afterLabel="Dopo proposto"
      />

      {proposal.conflicts.length > 0 && (
        <Alert variant="destructive">
          <AlertTitle>Pubblicazione bloccata</AlertTitle>
          {proposal.conflicts.map((conflict) => (
            <AlertDescription key={conflict}>{conflict}</AlertDescription>
          ))}
        </Alert>
      )}

      <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
        <Detail label="Confidenza">
          <span className="flex items-center gap-1">
            <ConfidenceBadge score={proposal.confidence} />
            <ConfidenceInfo />
          </span>
        </Detail>
        {proposal.analysisOutcome && (
          <Detail label="Ultimo esito analisi">
            {findingOutcomes[proposal.analysisOutcome] ??
              proposal.analysisOutcome}
          </Detail>
        )}
        <Detail label="Motivo" wide>
          {candidate.reason}
        </Detail>
        {context && (
          <Detail label="Contesto" wide>
            {context}
          </Detail>
        )}
        <Detail label="Lingua">{languageNames[candidate.language]}</Detail>
        <Detail label="Gruppi">
          {candidate.groupIds.length
            ? candidate.groupIds
                .map((id) => groupNames.get(id) ?? "Gruppo rimosso")
                .join(", ")
            : "Tutti i contesti"}
        </Detail>
        {proposal.rejectionReason && (
          <Detail label="Motivo del rifiuto precedente" wide>
            {proposal.rejectionReason}
          </Detail>
        )}
        {proposal.approvedAt !== null && (
          <Detail label="Approvata il">
            {new Date(proposal.approvedAt).toLocaleDateString("it-IT")}
          </Detail>
        )}
      </dl>

      <ReplaySummary
        rule={replay}
        sessions={replaySessions}
        pending={replayPending}
      />

      <Separator />

      <Section title={evidenceSummary(proposal.evidence)}>
        {proposal.evidence.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Le sessioni sorgenti sono state eliminate. La definizione approvata
            resta una regola indipendente.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {proposal.evidence.map((e, index) => (
              <li
                key={`${e.sessionId}-${e.revisionId}-${e.originalIndex}`}
                className="flex min-w-0 flex-col gap-2 rounded-lg bg-muted/50 p-3 text-sm"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">
                    Sessione #{e.sessionId}
                    {e.revisionId
                      ? ` · Revisione #${e.revisionId}`
                      : e.originalIndex !== null
                        ? ` · Originale ASR ${e.originalIndex + 1}`
                        : " · Risultato elaborato"}
                  </span>
                  <Badge variant={e.humanConfirmed ? "success" : "outline"}>
                    {e.humanConfirmed
                      ? "Correzione umana confermata"
                      : e.revisionId
                        ? "Conferma annullata"
                        : "Fonte osservata, ipotesi da valutare"}
                  </Badge>
                </div>
                <p
                  aria-label={`Fonte ${index + 1}`}
                  className="max-h-48 overflow-y-auto break-words whitespace-pre-wrap"
                >
                  {e.before}
                </p>
                {e.after && (
                  <p className="max-h-48 overflow-y-auto break-words whitespace-pre-wrap text-muted-foreground">
                    Correzione: {e.after}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Esempi e frasi di controllo">
        <p className="text-xs text-muted-foreground">
          Esempi della definizione, non conteggi di occorrenze.
        </p>
        <ul className="flex flex-col gap-2">
          {[
            ...candidate.positiveExamples.map((e) => ({
              ...e,
              label: "Da correggere",
            })),
            ...candidate.negativeExamples.map((e) => ({
              ...e,
              label: "Controllo",
            })),
          ].map((e, i) => (
            <li
              key={i}
              className="flex min-w-0 flex-col gap-1 rounded-lg bg-muted/50 p-3 text-sm"
            >
              <Badge variant="outline">{e.label}</Badge>
              <p className="break-words whitespace-pre-wrap">
                Prima: {e.input}
              </p>
              <p className="break-words whitespace-pre-wrap">
                Atteso: {e.output}
              </p>
            </li>
          ))}
        </ul>
      </Section>

      <div
        role="toolbar"
        aria-label="Azioni sulla proposta"
        className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t bg-background py-3"
      >
        {can.disable && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => onCommand("disable")}
          >
            <PowerOffIcon data-icon="inline-start" />
            Disattiva
          </Button>
        )}
        {can.showApprove && (
          <Button
            size="sm"
            disabled={busy || !can.approve}
            aria-keyshortcuts="A"
            onClick={() => onCommand("approve")}
          >
            <CheckIcon data-icon="inline-start" />
            {proposal.state === "disabled" ? "Riattiva" : "Approva"}
            <Kbd aria-hidden="true">A</Kbd>
          </Button>
        )}
        {proposal.approvedAt === null && (
          <>
            <Button
            size="sm"
              variant="outline"
              disabled={busy || !can.edit}
              aria-keyshortcuts="E"
              onClick={() => onCommand("edit")}
            >
              <PencilIcon data-icon="inline-start" />
              Modifica
              <Kbd aria-hidden="true">E</Kbd>
            </Button>
            <Button
            size="sm"
              variant="outline"
              disabled={busy || !can.defer}
              aria-keyshortcuts="R"
              onClick={() => onCommand("defer")}
            >
              <Clock3Icon data-icon="inline-start" />
              Rimanda
              <Kbd aria-hidden="true">R</Kbd>
            </Button>
            <Button
            size="sm"
              variant="ghost"
              disabled={busy || !can.reject}
              aria-keyshortcuts="X"
              onClick={() => onCommand("reject")}
            >
              <XIcon data-icon="inline-start" />
              Rifiuta
              <Kbd aria-hidden="true">X</Kbd>
            </Button>
          </>
        )}
      </div>
    </article>
  );
}

function Detail({
  label,
  wide = false,
  children,
}: {
  label: string;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={cn("flex flex-col gap-1", wide && "sm:col-span-2")}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="break-words">{children}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Collapsible className="flex flex-col gap-2">
      <CollapsibleTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="group/trigger justify-between self-stretch"
        >
          {title}
          <ChevronDownIcon
            data-icon="inline-end"
            className="transition-transform group-data-[state=open]/trigger:rotate-180"
          />
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="flex flex-col gap-2">
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}

function ReplaySummary({
  rule,
  sessions,
  pending,
}: {
  rule: RuleReplay | null;
  sessions: number;
  pending: boolean;
}) {
  if (!rule)
    return pending ? (
      <p className="text-sm text-muted-foreground">
        Riprova sullo storico in corso…
      </p>
    ) : null;
  return (
    <section
      aria-label="Riprova sullo storico"
      className="flex flex-col gap-1 text-sm"
    >
      <p>{replayHeadline(rule, sessions)}</p>
      {rule.changedSessions === 0 && !rule.active && (
        <p className="text-muted-foreground">
          Una regola senza effetto sullo storico non viene pubblicata in
          automatico.
        </p>
      )}
      {rule.usage && (
        <p className="text-muted-foreground">{usageHeadline(rule.usage)}</p>
      )}
      {rule.atPublication && (
        <p className="text-muted-foreground">
          Alla pubblicazione #{rule.atPublication.publication}:{" "}
          {rule.atPublication.changedSessions} su {rule.atPublication.sessions}.
        </p>
      )}
      {rule.excerpts.length > 0 && (
        <Section
          title={`Estratti prima e dopo (${rule.excerpts.length}${
            rule.changedSessions !== null &&
            rule.changedSessions > rule.excerpts.length
              ? ` di ${rule.changedSessions}`
              : ""
          })`}
        >
          <ul className="flex flex-col gap-2">
            {rule.excerpts.map((excerpt) => (
              <li
                key={excerpt.sessionId}
                className="flex flex-col gap-1 rounded-lg bg-muted/50 p-3"
              >
                <span className="text-xs text-muted-foreground">
                  Sessione #{excerpt.sessionId}
                </span>
                <p className="break-words whitespace-pre-wrap">
                  <span className="text-muted-foreground">
                    {rule.active ? "Senza la regola: " : "Oggi: "}
                  </span>
                  {excerpt.before}
                </p>
                <p className="break-words whitespace-pre-wrap">
                  <span className="text-muted-foreground">Con la regola: </span>
                  {excerpt.after}
                </p>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </section>
  );
}
