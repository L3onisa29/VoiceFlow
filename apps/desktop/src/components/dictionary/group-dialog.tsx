import { useEffect, useRef, useState } from "react";
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
import { useShortcut } from "@/hooks/use-shortcut";
import { useDraftClose } from "./discard-draft-dialog";
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
  const { requestClose, confirmation, confirming, draftDialogProps } =
    useDraftClose(name !== initial.name, busy, onClose);
  const formRef = useRef<HTMLFormElement>(null);
  useShortcut("mod+enter", () => formRef.current?.requestSubmit(), {
    allowInInputs: true,
    enabled: !confirming,
  });
  const errorRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);
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
          ref={formRef}
          className="flex flex-col gap-5"
          onSubmit={(event) => {
            event.preventDefault();
            if (busy || !name.trim()) return;
            onSave({ id: initial.id, name });
          }}
        >
          <FieldGroup>
            <Field data-disabled={busy || undefined}>
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
          {error && (
            <div ref={errorRef} tabIndex={-1}>
              <DictionaryError error={error} />
            </div>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={requestClose}
              disabled={busy}
            >
              Annulla
            </Button>
            <Button
              type="submit"
              disabled={busy || !name.trim()}
              aria-keyshortcuts="Meta+Enter"
            >
              {busy ? "Salvataggio…" : "Salva gruppo"}
            </Button>
          </DialogFooter>
        </form>
        {confirmation}
      </DialogContent>
    </Dialog>
  );
}
