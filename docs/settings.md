# Impostazioni

Le impostazioni seguono la struttura di [T3 Code](https://github.com/pingdotgg/t3code/tree/main/apps/web/src/components/settings),
adattata alle funzioni di VoiceFlow: sidebar dedicata, ricerca e una categoria
visibile alla volta. Ogni preferenza ha etichetta e descrizione a sinistra,
con il controllo a destra. Nelle finestre strette i controlli vanno sotto il testo
e la navigazione si dispone sopra il contenuto.

La ricerca filtra le categorie anche per contenuto, ad esempio "microfono",
"ragionamento" o "fascia oraria". I collegamenti spostano il focus sul titolo
e aggiornano l'indirizzo, così la categoria resta selezionata dopo un ricaricamento
e si può usare la navigazione avanti/indietro. Il pulsante **Torna a VoiceFlow**
riapre la dettatura. Le bozze di Analisi e Automazione restano disponibili durante
il passaggio tra categorie; per conservarle uscendo dalle impostazioni occorre salvarle.

- **Dettatura**: lingua, pulizia locale e formato. Le modifiche si salvano
  automaticamente; durante una dettatura o un'operazione i controlli sono bloccati.
- **Analisi**: consenso all'invio dei testi selezionati a OpenAI, approvazione
  automatica sopra 90, modello e ragionamento. Le modifiche diventano attive con
  **Salva preferenze analisi**, anche quando il consenso resta disattivato.
  **Avanzate · Limiti e utilizzo** contiene tutti e cinque i limiti e i contatori.
- **Automazione**: **Analisi programmata** e fascia oraria locale.
  **Salva pianificazione** applica la scelta. Il consenso OpenAI e la preferenza
  di approvazione automatica sono quelli salvati nel gruppo Analisi.
  Diagnostica e tempi di ripresa sono in **Avanzate · Diagnostica automazione**.
- **Sistema**: motore locale, permessi del Mac e scorciatoie. Se manca un
  permesso, il relativo pulsante porta all'intervento necessario. Il pulsante
  **Carica modello** appare se il modello è installato ma non caricato.

L'analisi è facoltativa. Comprende originali, testi elaborati, correzioni ricordate
non escluse e definizioni pertinenti. Il dettaglio dello Storico permette di
escludere una sessione. Salvare le preferenze interrompe i lotti in corso;
esclusione e cancellazione impediscono nuovi invii. Una richiesta già accettata
da OpenAI può proseguire e avere un costo; queste azioni non cancellano dati
remoti o regole già approvate. Le regole restano gestibili in Suggerimenti.

L'approvazione automatica può pubblicare regole soltanto con punteggio 91–100,
evidenze valide e senza conflitti o veti manuali. I punteggi 0–90 o sconosciuti
richiedono revisione. Il punteggio è una stima del provider sull'azione proposta.

Per avviare un solo lotto senza attendere la fascia, usa **Esegui ora** nella
pagina Analisi. Restano necessari consenso, componente di analisi disponibile,
budget e fonti preparabili. La preferenza di approvazione automatica vale anche
per questo avvio. Vedi [analisi](analysis.md) e [pianificazione](analysis-scheduler.md).
