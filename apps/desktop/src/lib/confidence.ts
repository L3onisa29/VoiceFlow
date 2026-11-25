/** Short level shown next to a score. */
export function confidenceLevel(score: number | null) {
  if (score === null) return "Non disponibile";
  if (score > 90) return "Molto alta";
  if (score >= 75) return "Alta";
  if (score >= 50) return "Media";
  return "Bassa";
}
export function confidenceVariant(score: number | null) {
  if (score === null) return "outline" as const;
  if (score > 90) return "success" as const;
  if (score >= 75) return "secondary" as const;
  if (score >= 50) return "outline" as const;
  return "warning" as const;
}
export const confidenceBands = [
  {
    range: "91–100",
    label: "Molto alta",
    note: "Approvabile in automatico, se l'opzione è attiva.",
  },
  { range: "75–90", label: "Alta", note: "Revisione manuale." },
  { range: "50–74", label: "Media", note: "Revisione manuale." },
  { range: "0–49", label: "Bassa", note: "Revisione manuale." },
] as const;
export const confidenceMeaning =
  "La confidenza è una stima del provider sull'azione proposta, non una misura dell'accuratezza ASR.";
export const findingActions = {
  keep: "Mantieni il testo",
  remove: "Rimuovi la disfluenza",
  replace: "Sostituisci il termine",
};
export const findingOutcomes: Record<string, string> = {
  "auto-approved": "Approvata automaticamente",
  "no-change": "Testo mantenuto · nessuna regola pubblicata",
  "manual-confidence": "Revisione manuale · confidenza fino a 90",
  "manual-policy": "Revisione manuale · automatismo disattivato",
  "manual-unknown": "Revisione manuale · confidenza sconosciuta",
  "blocked-human": "Automatismo bloccato · scelta manuale precedente",
  "blocked-legacy": "Revisione manuale · ipotesi precedente senza confidenza",
  "blocked-changed": "Revisione manuale · proposta modificata",
  "blocked-evidence":
    "Revisione manuale · evidenza precedente esclusa o non più valida",
  "blocked-conflict": "Revisione manuale · conflitto tra regole o esempi",
  "manual-no-effect":
    "Revisione manuale · la regola non cambia nessuna dettatura salvata",
  "already-active": "Regola già attiva · nessuna nuova pubblicazione",
};
