import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { DictionaryError } from "./dictionary-error";
import type { GroupInput } from "@/lib/dictionary";

type Props = {
  initial: GroupInput;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onReturnFocus: () => void;
  onSave: (input: GroupInput) => void;
};
export function GroupDialog({
  initial,
  busy,
  error,
  onClose,
  onReturnFocus,
  onSave,
}: Props) {
  const [name, setName] = useState(initial.name);
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
        showCloseButton={!busy}
      >
        <DialogHeader>
          <DialogTitle>
            {initial.id === null ? "Crea gruppo" : "Rinomina gruppo"}
          </DialogTitle>
          <DialogDescription>
            Organizza il dizionario per progetto, argomento o cliente.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-5"
          onSubmit={(event) => {
            event.preventDefault();
            onSave({ id: initial.id, name });
          }}
        >
          <FieldGroup>
            <Field data-disabled={busy}>
              <FieldLabel htmlFor="group-name">Nome del gruppo</FieldLabel>
              <Input
                id="group-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                disabled={busy}
                required
                maxLength={80}
                autoComplete="off"
              />
            </Field>
          </FieldGroup>
          {error && <DictionaryError error={error} />}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={busy}
            >
              Annulla
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy ? "Salvataggio…" : "Salva gruppo"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
