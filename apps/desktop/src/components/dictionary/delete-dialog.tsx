import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from "@/components/ui/alert-dialog";
import { DictionaryError } from "./dictionary-error";
export type DeleteTarget = {
  kind: "term" | "group";
  id: number;
  label: string;
  termCount?: number;
  globalCount?: number;
};
export function DeleteDialog({
  target,
  busy,
  error,
  onClose,
  onReturnFocus,
  onConfirm,
}: {
  target: DeleteTarget;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onReturnFocus: () => void;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <AlertDialogContent
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          onReturnFocus();
        }}
      >
        <AlertDialogHeader>
          <AlertDialogTitle>
            {target.kind === "group"
              ? "Eliminare il gruppo?"
              : "Eliminare la parola?"}
          </AlertDialogTitle>
          <AlertDialogDescription className="break-words">
            {target.kind === "group"
              ? `Il gruppo “${target.label}” verrà eliminato. ${target.termCount} ${target.termCount === 1 ? "parola resta" : "parole restano"} nel dizionario. ${target.globalCount} ${target.globalCount === 1 ? "diventa" : "diventano"} senza gruppo: le loro varianti saranno disponibili in tutte le prossime dettature. Le altre mantengono i propri gruppi. La sessione attiva non cambia.`
              : `La parola “${target.label}” verrà eliminata dal dizionario e da tutti i suoi gruppi.`}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && <DictionaryError error={error} />}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Annulla</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={busy}
            onClick={(event) => {
              event.preventDefault();
              onConfirm();
            }}
          >
            {busy ? "Eliminazione…" : "Elimina"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
