import { useRef, useState, type FocusEvent } from "react";
import { useBlocker } from "@tanstack/react-router";
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

export function useDraftClose(
  dirty: boolean,
  busy: boolean,
  onClose: () => void,
) {
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const discarded = useRef(false);
  const rememberFocus = (element: EventTarget | null) => {
    // A backdrop pointer-down can blur the editor before requesting close.
    // Keep its last focus target instead of replacing it with body or an alert.
    if (
      dialogRef.current &&
      element instanceof HTMLElement &&
      element.closest('[role="dialog"], [role="alertdialog"]') ===
        dialogRef.current
    ) {
      previousFocus.current = element;
    }
  };
  const blocker = useBlocker({
    shouldBlockFn: () => {
      if (dirty || busy) rememberFocus(document.activeElement);
      return dirty || busy;
    },
    enableBeforeUnload: dirty || busy,
    withResolver: true,
  });
  const requestClose = () => {
    if (busy) return;
    if (!dirty) return onClose();
    rememberFocus(document.activeElement);
    setConfirmDiscard(true);
  };
  const confirmation = (
    <AlertDialog
      open={confirmDiscard || blocker.status === "blocked"}
      onOpenChange={(open) => {
        setConfirmDiscard(open);
        if (!open) blocker.reset?.();
      }}
    >
      <AlertDialogContent
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          const dialog = dialogRef.current;
          if (discarded.current || !dialog?.isConnected) return;
          const candidates = [
            previousFocus.current,
            ...dialog.querySelectorAll<HTMLElement>(
              'input, textarea, select, [contenteditable="true"]',
            ),
            dialog,
          ];
          for (const candidate of candidates) {
            if (
              !candidate?.isConnected ||
              candidate.closest('[role="dialog"], [role="alertdialog"]') !==
                dialog ||
              candidate.matches(":disabled") ||
              !candidate.getClientRects().length
            )
              continue;
            candidate.focus({ preventScroll: true });
            if (document.activeElement === candidate) break;
          }
        }}
      >
        <AlertDialogHeader>
          <AlertDialogTitle>Scartare le modifiche?</AlertDialogTitle>
          <AlertDialogDescription>
            La bozza non è stata salvata. Puoi continuare a modificarla oppure
            scartarla.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>
            Continua a modificare
          </AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={busy}
            onClick={() => {
              discarded.current = true;
              blocker.proceed?.();
              onClose();
            }}
          >
            Scarta modifiche
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
  return {
    requestClose,
    confirmation,
    /** The discard confirmation is open: the editor must not save or close. */
    confirming: confirmDiscard || blocker.status === "blocked",
    draftDialogProps: {
      ref: dialogRef,
      onFocusCapture: (event: FocusEvent<HTMLDivElement>) =>
        rememberFocus(event.target),
    },
  };
}
