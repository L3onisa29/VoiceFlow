export function confidenceBand(score: number | null) {
  if (score === null) return "Non disponibile · revisione manuale";
  if (score > 90) return "Molto alta · 91–100";
  if (score >= 75) return "Alta · 75–90 · revisione manuale";
  if (score >= 50) return "Media · 50–74 · revisione manuale";
  return "Bassa · 0–49 · revisione manuale";
}
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
  "already-active": "Regola già attiva · nessuna nuova pubblicazione",
};
