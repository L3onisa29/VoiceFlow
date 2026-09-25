# Storico locale e analytics

La pagina Dettatura mostra lo storico sotto la trascrizione e i controlli di registrazione. L'interfaccia usa i componenti shadcn/ui già adottati dal progetto.

## Archivio

Il backend usa `rusqlite` con SQLite incluso nell'app. Il file è `~/Library/Application Support/VoiceFlow/history.sqlite3`, con permessi `0600`. Il dizionario continua a usare il suo database, senza migrazioni dei dati esistenti. Non servono account, servizi esterni o connessioni di rete.

Ogni nuova sessione conserva separatamente originale ASR, risultato elaborato e testo corrente dopo le modifiche dell'utente. Conserva anche data di inizio, durata senza pause, lingua configurata, numero di parole e metadati di elaborazione. Non conserva audio. Il salvataggio avviene nel worker Rust dopo la finalizzazione e il tentativo di incolla, anche se l'incolla fallisce. Non dipende dalla pagina aperta o dagli eventi ricevuti dalla UI. Le sessioni annullate prima del completamento e quelle senza testo, sia ASR sia elaborato, non vengono archiviate. Una sessione contenente soltanto marcatori ASR può avere un risultato elaborato vuoto. Gli errori di acquisizione con solo testo parziale restano recuperabili nell'anteprima, ma non entrano nello storico delle dettature concluse.

Una chiave unica della sessione impedisce duplicazioni se il salvataggio viene ripetuto. Gli errori di archivio non impediscono l'incolla e compaiono nella sezione dello storico. Il testo corrente resta copiabile. Un arresto improvviso prima del salvataggio può perdere la sessione corrente; non è implementato il recupero delle registrazioni interrotte.

Lo schema SQLite v2 migra transazionalmente le righe v1. Il vecchio testo diventa anche il risultato elaborato originale; grezzo e metadati mancanti restano `NULL`, mostrati come non disponibili. Nessun filtro modifica retroattivamente il testo precedente.

## Originali, correzioni e conferme

L'originale ASR è un array ordinato di stringhe esatte del decoder, una per segmento concluso prima del reset per pausa, silenzio o stop. Le revisioni intermedie del decoder vengono sostituite in memoria; non vengono archiviate. I confini dei segmenti restano disponibili per rielaborare separatamente comandi e pulizia. L'originale può contenere marcatori rimossi dal risultato. Non viene duplicato negli eventi di anteprima frequenti.

Lingua e metadati vengono fissati all'avvio della sessione. La versione iniziale registra modello, revisione prevista dal manifest di download, versione decoder, processore, formato e opzione/versione della pulizia. La provenienza dichiara che i file runtime non sono stati nuovamente verificati tramite hash; la revisione prevista non prova che file sostituiti manualmente siano identici. I gruppi sono inizialmente vuoti e la versione del dizionario è assente perché B1 non applica ancora sostituzioni. B2 e C devono inserire qui le versioni degli snapshot effettivamente applicati.

Nel dialogo dello storico, **Salva correzione** crea una revisione esplicita con prima/dopo e aggiorna il testo usato da ricerca e copia. Non cambia risultato elaborato, originale, lingua, durata o conteggi. Una versione attesa impedisce di sovrascrivere un aggiornamento concorrente; la chiave di richiesta rende ripetibile un salvataggio dopo un errore di risposta.

**Ricorda questa correzione** richiede una categoria scelta dall'utente: riconoscimento, pulizia o stile. Conserva un esempio confermato distinto dalle semplici revisioni. Non pubblica regole e non deduce sostituzioni da una riscrittura libera. Un output del processore non è una conferma umana. **Annulla ultima correzione** ripristina la revisione precedente, o il risultato elaborato quando non restano revisioni, e rimuove la conferma relativa alla revisione annullata. La sequenza delle revisioni, comprese quelle annullate, resta visibile nel dialogo.

## Cancellazione e dati derivati

Eliminare una sessione rimuove atomicamente la sua riga, tutti gli originali e risultati, le revisioni e le conferme tramite chiavi esterne con cancellazione a cascata. SQLite usa `foreign_keys=ON` e `secure_delete=ON`. Non rimangono copie testuali nelle tabelle di evidenza B1. Questo non promette cancellazione da backup del Mac o da servizi esterni a cui il testo sia stato inviato.

Le integrazioni B2/C/D devono mantenere il riferimento alla sessione/revisione sorgente e cancellare anche le proprie copie testuali derivate o usare riferimenti senza copie. Non possono presentare come cancellati dati già accettati da un fornitore esterno senza una capacità effettiva di rimozione. Le regole approvate future richiedono una policy esplicita per i testi e le evidenze provenienti da sessioni eliminate.

## Interfaccia e misure

- Lista dalla sessione più recente, 20 elementi per pagina con cursore stabile.
- Ricerca letterale senza distinzione tra maiuscole e minuscole, anche con lettere accentate.
- Apertura di originale, risultato, revisioni e metadati; modifica e annullamento; conferma categorizzata; copia del testo salvato ed eliminazione con conferma.
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

`cargo test -p voiceflow-desktop history::tests --locked` verifica database temporanei reali: riapertura, migrazione v1, rollback dello schema e delle modifiche, originali immutabili, correzioni multiple, conferme, annullamento, permessi, salvataggio ripetuto, conteggi, ricerca, paginazione, cancellazione dei derivati e rifiuto di database corrotti o di versioni future.

Le prove della UI sulla build di produzione usano IPC Tauri simulato; non sostituiscono la prova con microfono, incolla e app macOS reale.

Prove e comandi di riproduzione B1 sono in [voiceflow-v1-b1.md](verification/voiceflow-v1-b1.md).
