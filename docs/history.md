# Storico locale e analytics

La pagina Dettatura mostra lo storico sotto la trascrizione e i controlli di registrazione. L'interfaccia usa i componenti shadcn/ui già adottati dal progetto.

## Archivio

Il backend usa `rusqlite` con SQLite incluso nell'app. Il file è `~/Library/Application Support/VoiceFlow/history.sqlite3`, con permessi `0600`. Il dizionario continua a usare il suo database, senza migrazioni dei dati esistenti. Non servono account, servizi esterni o connessioni di rete.

Ogni sessione conserva testo finale, data di inizio, durata senza pause, lingua configurata e numero di parole. Non conserva audio. Il salvataggio avviene nel worker Rust dopo la finalizzazione e il tentativo di incolla, anche se l'incolla fallisce. Non dipende dalla pagina aperta o dagli eventi ricevuti dalla UI. Le sessioni annullate prima del completamento e quelle senza testo non vengono archiviate. Gli errori di acquisizione con solo testo parziale restano recuperabili nell'anteprima, ma non entrano nello storico delle dettature concluse.

Una chiave unica della sessione impedisce duplicazioni se il salvataggio viene ripetuto. Gli errori di archivio non impediscono l'incolla e compaiono nella sezione dello storico. Il testo corrente resta copiabile. Un arresto improvviso prima del salvataggio può perdere la sessione corrente; non è implementato il recupero delle registrazioni interrotte.

Lo storico parte dalle nuove dettature. Le trascrizioni prodotte dalle versioni precedenti non erano persistite e non vengono ricostruite.

## Interfaccia e misure

- Lista dalla sessione più recente, 20 elementi per pagina con cursore stabile.
- Ricerca letterale senza distinzione tra maiuscole e minuscole, anche con lettere accentate.
- Apertura del testo completo, copia negli appunti ed eliminazione con conferma.
- Aggiornamento dopo ogni salvataggio e al ritorno nell'app. Le risposte di ricerche precedenti non possono sovrascrivere quella corrente.
- Totali calcolati da tutte le righe conservate, indipendentemente da ricerca e paginazione. Eliminare una trascrizione aggiorna anche i totali.

Le parole sono segmenti separati da spazi contenenti almeno una lettera o un numero. È una misura adatta a italiano e inglese; la sola punteggiatura non conta. La velocità è `60 × parole / secondi` sull'intero storico, con durata positiva. Include i silenzi durante la registrazione ed esclude le pause esplicite. Non è la velocità del solo parlato. Senza durate utilizzabili appare "Non disponibile".

## Varianti per gli analytics

La [documentazione di Wispr Flow](https://docs.wisprflow.ai/articles/8760230576-your-usage-tab-track-your-dictation-stats-in-wispr-flow), consultata il 25 settembre 2026, descrive parole totali, velocità recente, correzioni, distribuzione dell'uso per app e calendario con serie di giorni attivi. La sua velocità usa fino a 100 trascrizioni recenti e, quando disponibile, esclude il silenzio; non è direttamente confrontabile con la media di VoiceFlow.

| Variante     | Contenuto                                                           | Stato e dati necessari                                                                                                                          |
| ------------ | ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Essenziale   | Parole, sessioni, durata, parole al minuto, storico ricercabile     | Implementata, tutti i dati restano sul Mac                                                                                                      |
| Abitudini    | Andamento giornaliero e settimanale, calendario, giorni consecutivi | Proposta; ricavabile dalle date salvate, definendo il fuso locale per i raggruppamenti                                                          |
| Produttività | Distribuzione per app e tempo risparmiato                           | Proposta; richiede identificare l'app di destinazione e una velocità di digitazione configurabile. Il risparmio deve essere indicato come stima |

La variante Abitudini è il passo successivo consigliato: usa dati già presenti senza raccogliere altro. Correzioni automatiche e qualità non sono misurate ora: contare la differenza tra testo parziale e finale darebbe un dato fuorviante.

## Verifica

`cargo test -p voiceflow-desktop history::tests --locked` verifica database temporanei reali: riapertura, permessi, salvataggio ripetuto, conteggi, ricerca Unicode e letterale, paginazione durante nuovi inserimenti, eliminazione e rifiuto di database corrotti o di versioni future.

Le prove della UI sulla build di produzione usano IPC Tauri simulato; non sostituiscono la prova con microfono, incolla e app macOS reale.
