// Dependency-free so that errors.ts (and its Node test) can import it.
export const analysisErrors: Record<string, string> = {
  "ingest-storage":
    "Errore di archiviazione. Le proposte già salvate restano disponibili; il lotto può essere riprovato.",
  "provider-unavailable":
    "Codex CLI o Node non disponibile. Verifica l'installazione.",
  "provider-exit":
    "Codex CLI non ha completato la richiesta. Verifica rete, accesso a Codex e disponibilità del modello e del livello di ragionamento scelti.",
  "provider-io": "Comunicazione con Codex CLI interrotta.",
  "invalid-output":
    "Risposta incompleta o non valida. Nessuna proposta importata.",
  "output-limit": "La risposta ha superato il limite di dimensione.",
  "candidate-rejected":
    "Alcune proposte non hanno superato la validazione. Le altre sono disponibili nei suggerimenti.",
  "all-candidates-rejected":
    "Nessuna proposta ha superato la validazione. Le sorgenti restano da analizzare; il lotto può essere riprovato.",
  "rejected-sources-parked":
    "Lotto sostituito dopo due tentativi con tutte le proposte scartate. Le fonti citate dalle proposte scartate sono state accantonate, le altre proseguono in un nuovo lotto.",
  "rejected-split":
    "Lotto diviso in due dopo due tentativi con tutte le proposte scartate. Nessuna fonte è stata persa.",
  timeout: "Tempo massimo raggiunto. Nessuna nuova regola attivata.",
  cancelled: "Analisi interrotta.",
  "privacy-changed":
    "Lotto annullato dopo una modifica alle impostazioni o alle esclusioni.",
  "source-changed":
    "Le sorgenti del lotto sono state eliminate o modificate. Il worker preparerà un nuovo lotto.",
  interrupted:
    "Esecuzione interrotta. Il worker potrà riprendere entro i limiti impostati.",
};
