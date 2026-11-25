import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldSet,
  FieldLegend,
  FieldDescription,
  FieldError,
  FieldSeparator,
} from "@/components/ui/field";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from "@/components/ui/collapsible";
import { useShortcut } from "@/hooks/use-shortcut";
import { useDraftClose } from "./discard-draft-dialog";
import { DictionaryError } from "./dictionary-error";
import { languageNames, type Group, type TermInput } from "@/lib/dictionary";

type Props = {
  initial: TermInput;
  /** Defaults to "Aggiungi parola" or "Modifica parola". */
  title?: string;
  groups: Group[];
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onReturnFocus: () => void;
  onSave: (input: TermInput) => void;
};

const lines = (value: string) => [
  ...new Set(
    value
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean),
  ),
];
const invalidWord = (value: string) => /\s/u.test(value.trim());
const ONE_WORD = "Inserisci una sola parola.";

export function TermDialog({
  initial,
  title,
  groups,
  busy,
  error,
  onClose,
  onReturnFocus,
  onSave,
}: Props) {
  const [text, setText] = useState(initial.text);
  const [groupIds, setGroupIds] = useState(initial.groupIds);
  const [variants, setVariants] = useState(initial.variants.join("\n"));
  const [language, setLanguage] = useState(initial.language);
  const [matching, setMatching] = useState(initial.matching);
  const [before, setBefore] = useState(initial.context.before ?? "");
  const [after, setAfter] = useState(initial.context.after ?? "");
  const [exceptBefore, setExceptBefore] = useState(
    initial.context.exceptBefore.join("\n"),
  );
  const [exceptAfter, setExceptAfter] = useState(
    initial.context.exceptAfter.join("\n"),
  );
  const formRef = useRef<HTMLFormElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);
  const dirty =
    text !== initial.text ||
    variants !== initial.variants.join("\n") ||
    language !== initial.language ||
    matching !== initial.matching ||
    before !== (initial.context.before ?? "") ||
    after !== (initial.context.after ?? "") ||
    exceptBefore !== initial.context.exceptBefore.join("\n") ||
    exceptAfter !== initial.context.exceptAfter.join("\n") ||
    [...groupIds].sort().join(",") !== [...initial.groupIds].sort().join(",");
  const { requestClose, confirmation, confirming, draftDialogProps } =
    useDraftClose(dirty, busy, onClose);
  // ⌘↵ saves from any field, like the submit button.
  useShortcut("mod+enter", () => formRef.current?.requestSubmit(), {
    allowInInputs: true,
    enabled: !confirming,
  });
  const variantLines = lines(variants);
  const tooLong = variantLines.some(
    (variant) =>
      variant.split(/\s+/u).length +
        Number(Boolean(after.trim() || exceptAfter.trim())) >
      7,
  );
  const contextErrors = {
    before: invalidWord(before),
    after: invalidWord(after),
    exceptBefore: lines(exceptBefore).some(invalidWord),
    exceptAfter: lines(exceptAfter).some(invalidWord),
  };
  const invalidContext = Object.values(contextErrors).some(Boolean);
  const invalid = !text.trim() || tooLong || invalidContext;
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) requestClose();
      }}
    >
      <DialogContent
        {...draftDialogProps}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          onReturnFocus();
        }}
        className="flex max-h-[calc(100dvh-2rem)] flex-col overflow-hidden sm:max-w-xl"
        showCloseButton={!busy}
      >
        <DialogHeader>
          <DialogTitle>
            {title ??
              (initial.id === null ? "Aggiungi parola" : "Modifica parola")}
          </DialogTitle>
          <DialogDescription>
            Conferma le varianti da sostituire con la grafia preferita. Le
            modifiche valgono dalla prossima dettatura.
          </DialogDescription>
        </DialogHeader>
        <form
          ref={formRef}
          className="flex min-h-0 flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (busy || invalid) return;
            onSave({
              id: initial.id,
              text,
              groupIds,
              variants: variantLines,
              language,
              matching,
              context: {
                before: before.trim() || null,
                after: after.trim() || null,
                exceptBefore: lines(exceptBefore),
                exceptAfter: lines(exceptAfter),
              },
            });
          }}
        >
          <FieldGroup className="min-h-0 overflow-y-auto px-1 pb-1">
            <Field data-disabled={busy || undefined}>
              <FieldLabel htmlFor="term-text">Grafia preferita</FieldLabel>
              <Input
                id="term-text"
                value={text}
                onChange={(event) => setText(event.target.value)}
                required
                maxLength={200}
                disabled={busy}
                autoComplete="off"
                placeholder="Es. nome di un progetto"
              />
            </Field>
            <Field data-invalid={tooLong || undefined}>
              <FieldLabel htmlFor="term-variants">
                Varianti confermate, una per riga
              </FieldLabel>
              <Textarea
                id="term-variants"
                value={variants}
                onChange={(event) => setVariants(event.target.value)}
                disabled={busy}
                aria-invalid={tooLong}
                rows={3}
                placeholder="Es. voice flow"
              />
              <FieldDescription>
                Solo queste espressioni saranno sostituite. Senza varianti la
                parola resta un promemoria.
              </FieldDescription>
              {tooLong && (
                <FieldError>
                  Non applicabile: massimo 7 parole, incluso il contesto
                  successivo.
                </FieldError>
              )}
            </Field>
            <FieldSet disabled={busy}>
              <FieldLegend variant="label">Gruppi</FieldLegend>
              {groups.length ? (
                <FieldGroup
                  data-slot="checkbox-group"
                  className="max-h-28 gap-3 overflow-y-auto"
                >
                  {groups.map((group) => (
                    <Field key={group.id} orientation="horizontal">
                      <Checkbox
                        id={`term-group-${group.id}`}
                        checked={groupIds.includes(group.id)}
                        onCheckedChange={(checked) =>
                          setGroupIds((ids) =>
                            checked === true
                              ? [...ids, group.id]
                              : ids.filter((id) => id !== group.id),
                          )
                        }
                        disabled={busy}
                      />
                      <FieldLabel
                        htmlFor={`term-group-${group.id}`}
                        className="min-w-0 break-words"
                      >
                        {group.name}
                      </FieldLabel>
                    </Field>
                  ))}
                </FieldGroup>
              ) : (
                <FieldDescription>
                  Nessun gruppo. Senza gruppo la parola vale in ogni dettatura.
                </FieldDescription>
              )}
            </FieldSet>
            <FieldSeparator />
            <Collapsible>
              <CollapsibleTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  className="group w-full justify-between"
                >
                  Lingua e condizioni avanzate
                  <ChevronDown
                    data-icon="inline-end"
                    className="transition-transform group-data-[state=open]:rotate-180"
                  />
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent className="pt-3">
                <FieldGroup>
                  <FieldGroup className="grid grid-cols-2 gap-3">
                    <Field>
                      <FieldLabel htmlFor="term-language">Lingua</FieldLabel>
                      <Select
                        value={language}
                        onValueChange={(value) =>
                          setLanguage(value as TermInput["language"])
                        }
                        disabled={busy}
                      >
                        <SelectTrigger id="term-language" className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectGroup>
                            <SelectItem value="*">Tutte</SelectItem>
                            <SelectItem value="it">
                              {languageNames.it}
                            </SelectItem>
                            <SelectItem value="en">
                              {languageNames.en}
                            </SelectItem>
                          </SelectGroup>
                        </SelectContent>
                      </Select>
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="term-matching">
                        Corrispondenza
                      </FieldLabel>
                      <Select
                        value={matching}
                        onValueChange={(value) =>
                          setMatching(value as TermInput["matching"])
                        }
                        disabled={busy}
                      >
                        <SelectTrigger id="term-matching" className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectGroup>
                            <SelectItem value="exact">
                              Esatta, incluse maiuscole
                            </SelectItem>
                            <SelectItem value="unicode-lowercase">
                              Ignora maiuscole Unicode
                            </SelectItem>
                          </SelectGroup>
                        </SelectContent>
                      </Select>
                    </Field>
                  </FieldGroup>
                  <FieldDescription>
                    Accenti e apostrofi restano distinti. La punteggiatura
                    finale viene conservata.
                  </FieldDescription>
                  <FieldSet disabled={busy}>
                    <FieldLegend variant="label">
                      Condizioni ed eccezioni facoltative
                    </FieldLegend>
                    <FieldDescription>
                      Parole adiacenti nell'originale. Ogni condizione contiene
                      una sola parola, incluse maiuscole e punteggiatura.
                    </FieldDescription>
                    <FieldGroup className="grid grid-cols-2 gap-3">
                      <Field data-invalid={contextErrors.before || undefined}>
                        <FieldLabel htmlFor="term-before">Solo dopo</FieldLabel>
                        <Input
                          id="term-before"
                          value={before}
                          onChange={(event) => setBefore(event.target.value)}
                          disabled={busy}
                          aria-invalid={contextErrors.before}
                        />
                        {contextErrors.before && (
                          <FieldError>{ONE_WORD}</FieldError>
                        )}
                      </Field>
                      <Field data-invalid={contextErrors.after || undefined}>
                        <FieldLabel htmlFor="term-after">
                          Solo prima di
                        </FieldLabel>
                        <Input
                          id="term-after"
                          value={after}
                          onChange={(event) => setAfter(event.target.value)}
                          disabled={busy}
                          aria-invalid={contextErrors.after}
                        />
                        {contextErrors.after && (
                          <FieldError>{ONE_WORD}</FieldError>
                        )}
                      </Field>
                      <Field
                        data-invalid={contextErrors.exceptBefore || undefined}
                      >
                        <FieldLabel htmlFor="term-except-before">
                          Escludi dopo, una per riga
                        </FieldLabel>
                        <Textarea
                          id="term-except-before"
                          value={exceptBefore}
                          onChange={(event) =>
                            setExceptBefore(event.target.value)
                          }
                          disabled={busy}
                          aria-invalid={contextErrors.exceptBefore}
                          rows={2}
                        />
                        {contextErrors.exceptBefore && (
                          <FieldError>Una sola parola per riga.</FieldError>
                        )}
                      </Field>
                      <Field
                        data-invalid={contextErrors.exceptAfter || undefined}
                      >
                        <FieldLabel htmlFor="term-except-after">
                          Escludi prima di, una per riga
                        </FieldLabel>
                        <Textarea
                          id="term-except-after"
                          value={exceptAfter}
                          onChange={(event) =>
                            setExceptAfter(event.target.value)
                          }
                          disabled={busy}
                          aria-invalid={contextErrors.exceptAfter}
                          rows={2}
                        />
                        {contextErrors.exceptAfter && (
                          <FieldError>Una sola parola per riga.</FieldError>
                        )}
                      </Field>
                    </FieldGroup>
                  </FieldSet>
                </FieldGroup>
              </CollapsibleContent>
            </Collapsible>
          </FieldGroup>
          {error && (
            <div ref={errorRef} tabIndex={-1}>
              <DictionaryError error={error} />
            </div>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={requestClose}
            >
              Annulla
            </Button>
            <Button
              type="submit"
              disabled={busy || invalid}
              aria-keyshortcuts="Meta+Enter"
            >
              {busy
                ? "Salvataggio…"
                : variantLines.length
                  ? "Conferma varianti e salva"
                  : "Salva parola"}
            </Button>
          </DialogFooter>
        </form>
        {confirmation}
      </DialogContent>
    </Dialog>
  );
}
