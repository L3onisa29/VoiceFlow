import { useState } from "react";
import { Button } from "@/components/ui/button";
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
        className="max-h-[85vh] overflow-y-auto"
        showCloseButton={!busy}
      >
        <DialogHeader>
          <DialogTitle>
            {initial.id === null ? "Aggiungi parola" : "Modifica parola"}
          </DialogTitle>
          <DialogDescription>
            Salva una parola o un’espressione. Puoi assegnarla a più gruppi.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-5"
          onSubmit={(event) => {
            event.preventDefault();
            onSave({ id: initial.id, text, groupIds });
          }}
        >
          <FieldGroup>
            <Field data-disabled={busy}>
              <FieldLabel htmlFor="term-text">Parola o espressione</FieldLabel>
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
            <Button type="submit" disabled={busy || !text.trim()}>
              {busy ? "Salvataggio…" : "Salva parola"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
