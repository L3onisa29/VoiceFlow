import { CircleAlert, RotateCcw } from "lucide-react";
import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export function DictionaryError({
  error,
  title = "Dizionario non aggiornato",
  onRetry,
}: {
  error: string;
  title?: string;
  onRetry?: () => void;
}) {
  return (
    <Alert variant="destructive">
      <CircleAlert />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="break-words">{error}</AlertDescription>
      {onRetry && (
        <AlertAction>
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RotateCcw data-icon="inline-start" />
            Riprova
          </Button>
        </AlertAction>
      )}
    </Alert>
  );
}
