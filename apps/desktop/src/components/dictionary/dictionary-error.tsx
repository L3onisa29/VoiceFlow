import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
export function DictionaryError({ error }: { error: string }) {
  return (
    <Alert variant="destructive">
      <AlertTitle>Dizionario non aggiornato</AlertTitle>
      <AlertDescription className="break-words">{error}</AlertDescription>
    </Alert>
  );
}
