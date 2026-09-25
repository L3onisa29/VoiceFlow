import { useState } from "react";
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
} from "@/components/ui/field";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { DictionaryError } from "./dictionary-error";
import type { Group, TermInput } from "@/lib/dictionary";

type Props = {
  initial: TermInput;
  groups: Group[];
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onReturnFocus: () => void;
  onSave: (input: TermInput) => void;
};
export function TermDialog({
  initial,
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
  const lines = (value: string) => [
    ...new Set(
      value
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean),
    ),
  ];
  const variantLines = lines(variants);
  const tooLong = variantLines.some(
    (variant) =>
      variant.split(/\s+/u).length +
        Number(Boolean(after.trim() || exceptAfter.trim())) >
      7,
  );
  const invalidContext = [
    before,
    after,
    ...lines(exceptBefore),
    ...lines(exceptAfter),
  ].some((word) => word.trim() && /\s/u.test(word.trim()));
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          onReturnFocus();
        }}
        className="max-h-[85vh] overflow-y-auto sm:max-w-xl"
        showCloseButton={!busy}
      >
        <DialogHeader>
          <DialogTitle>
            {initial.id === null ? "Aggiungi parola" : "Modifica parola"}
          </DialogTitle>
          <DialogDescription>
            Conferma le varianti da sostituire con la grafia preferita. Le
            modifiche valgono dalla prossima dettatura.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-5"
          onSubmit={(event) => {
            event.preventDefault();
            if (tooLong || invalidContext) return;
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
          <FieldGroup>
            <Field data-disabled={busy}>
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
            <Field data-invalid={tooLong}>
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
                {tooLong
                  ? "Non applicabile: massimo 7 parole, incluso il contesto successivo."
                  : "Solo queste espressioni saranno sostituite. Senza varianti la parola resta un promemoria. Massimo 7 parole per variante."}
              </FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="term-language">Lingua</FieldLabel>
              <Select
                value={language}
                onValueChange={(value) =>
                  setLanguage(value as TermInput["language"])
                }
                disabled={busy}
              >
                <SelectTrigger id="term-language">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectItem value="*">Tutte</SelectItem>
                    <SelectItem value="it">Italiano</SelectItem>
                    <SelectItem value="en">Inglese</SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel htmlFor="term-matching">Corrispondenza</FieldLabel>
              <Select
                value={matching}
                onValueChange={(value) =>
                  setMatching(value as TermInput["matching"])
                }
                disabled={busy}
              >
                <SelectTrigger id="term-matching">
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
              <FieldDescription>
                Accenti e apostrofi restano distinti. La punteggiatura finale
                viene conservata.
              </FieldDescription>
            </Field>
            <FieldSet disabled={busy}>
              <FieldLegend>Condizioni ed eccezioni facoltative</FieldLegend>
              <FieldDescription>
                Parole adiacenti nell'originale. Ogni condizione contiene una
                sola parola, incluse eventuali maiuscole e punteggiatura.
              </FieldDescription>
              <FieldGroup className="grid grid-cols-2 gap-3">
                <Field data-invalid={invalidContext}>
                  <FieldLabel htmlFor="term-before">Solo dopo</FieldLabel>
                  <Input
                    id="term-before"
                    value={before}
                    onChange={(event) => setBefore(event.target.value)}
                    disabled={busy}
                    aria-invalid={invalidContext}
                  />
                </Field>
                <Field data-invalid={invalidContext}>
                  <FieldLabel htmlFor="term-after">Solo prima di</FieldLabel>
                  <Input
                    id="term-after"
                    value={after}
                    onChange={(event) => setAfter(event.target.value)}
                    disabled={busy}
                    aria-invalid={invalidContext}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="term-except-before">
                    Escludi dopo, una per riga
                  </FieldLabel>
                  <Textarea
                    id="term-except-before"
                    value={exceptBefore}
                    onChange={(event) => setExceptBefore(event.target.value)}
                    disabled={busy}
                    rows={2}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="term-except-after">
                    Escludi prima di, una per riga
                  </FieldLabel>
                  <Textarea
                    id="term-except-after"
                    value={exceptAfter}
                    onChange={(event) => setExceptAfter(event.target.value)}
                    disabled={busy}
                    rows={2}
                  />
                </Field>
              </FieldGroup>
              {invalidContext && (
                <FieldDescription>
                  Inserisci una sola parola per condizione o eccezione.
                </FieldDescription>
              )}
            </FieldSet>
            <FieldSet disabled={busy}>
              <FieldLegend>Gruppi</FieldLegend>
              {groups.length ? (
                <FieldGroup className="max-h-52 gap-3 overflow-y-auto">
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
                  Nessun gruppo. Puoi crearne uno dopo aver salvato la parola.
                </FieldDescription>
              )}
            </FieldSet>
          </FieldGroup>
          {error && <DictionaryError error={error} />}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={onClose}
            >
              Annulla
            </Button>
            <Button
              type="submit"
              disabled={busy || !text.trim() || tooLong || invalidContext}
            >
              {busy
                ? "Salvataggio…"
                : variantLines.length
                  ? "Conferma varianti e salva"
                  : "Salva parola"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
