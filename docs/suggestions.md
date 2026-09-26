# Suggerimenti e regole approvate

La pagina Suggerimenti separa le ipotesi di analisi, gli esempi ricordati nello storico e le regole approvate. Una riscrittura libera rimane un esempio. Non genera automaticamente una sostituzione.

Ogni proposta mostra l'ultima confidenza disponibile, la fascia e l'esito dell'analisi, oltre a categoria, prima/dopo, operazione, lingua, gruppi, condizioni, eccezioni, motivazione, frasi di controllo, riferimenti allo storico e conflitti. Il numero di riferimenti conta le fonti collegate; il numero di sessioni usa ID distinti. Nessuno dei due è una frequenza di occorrenze. Le correzioni umane sono conteggiate solo se ricordate e non annullate. Un risultato già prodotto dal processore non è una conferma umana.

## Revisione

- **Approva** apre la definizione da autorizzare. **Approva e pubblica** verifica le regole combinate e crea una nuova versione atomica. La regola vale dalla prossima sessione.
- **Modifica** cambia una proposta ancora non approvata. Consente termini, varianti, operazioni di pulizia supportate, lingua, gruppi, condizioni, eccezioni, motivazione ed esempi. Il salvataggio non attiva regole. Gli esempi aggiunti possono essere rimossi, mantenendone almeno uno positivo e uno di controllo.
- **Rifiuta** richiede un motivo. La stessa proposta con le stesse fonti resta rifiutata. Una nuova sessione o una diversa revisione umana ricordata possono riaprirla per revisione manuale; il veto all'approvazione automatica rimane. Un'altra vista della stessa sessione, per esempio originale e risultato elaborato, non basta.
- **Rimanda** conserva la proposta per una revisione successiva.
- **Disattiva** pubblica una versione senza quella regola. L'approvazione precedente rimane tracciata. **Riattiva** richiede una nuova pubblicazione esplicita.
- **Ripristina** crea una nuova versione con le regole approvate di una pubblicazione precedente. Si può tornare alla sola pulizia incorporata. Il dizionario modificato manualmente conserva la propria versione e viene ricontrollato insieme alla configurazione da ripristinare.

Le definizioni già approvate sono immutabili. Per modificarne il comportamento serve una nuova proposta; la vecchia può essere disattivata. Pausa/ripresa mantiene lo snapshot iniziale. Nessuna pubblicazione o ripristino modifica trascrizioni passate.

Un conflitto fra varianti, anche con il dizionario manuale, oppure un esempio che cambia risultato nella configurazione combinata blocca la pubblicazione. Il backend esegue gli esempi tramite `TextProcessor::with_rules`, includendo comandi, catalogo incorporato, pulizia approvata e dizionario. Verifica lingua italiana, inglese e automatica con nessun gruppo, gruppi attuali, tutti i gruppi e singoli gruppi. Le frasi fisse di controllo proteggono negazioni, numeri, nomi e ripetizioni intenzionali. Sono prove circoscritte, non una garanzia semantica su ogni frase o combinazione di gruppi.

La UI può usare le regole mentre una dettatura è attiva. Il worker legge insieme dizionario e pubblicazioni prima della cattura e ne conserva una copia immutabile. In caso di lettura fallita usa la pulizia incorporata e nessuna regola personale, con avviso visibile.

## Conservazione e cancellazione

Proposte, riferimenti, audit e pubblicazioni risiedono nello stesso `history.sqlite3` dello storico. I riferimenti contengono solo ID di sessione/revisione/segmento. La UI risolve il testo al momento della lettura.

Eliminare una sessione elimina tutte le proposte non approvate che la citano, anche se hanno altre fonti, insieme a motivazioni, esempi, rifiuti e audit associati. Le chiavi esterne eliminano i riferimenti alla sessione anche dalle regole approvate. Lo storico usa `secure_delete`.

L'approvazione rende la definizione una regola indipendente dell'utente. La finestra di approvazione dichiara che motivazione, esempi e versioni della definizione rimangono disponibili dopo la cancellazione delle fonti. I testi completi delle fonti non sono copiati nelle evidenze o nell'audit. Gli hash SHA-256 delle richieste permettono il retry senza conservare vecchi payload di modifica o motivi di rifiuto nascosti. Le definizioni approvate rimangono anche quando disattivate, per riproducibilità e ripristino.

Annullare una correzione elimina le proposte non approvate sostenute da quella revisione, anche se hanno altre fonti, insieme a motivazioni, esempi, riferimenti, audit e rilievi collegati. I veti manuali persistono come hash. Una regola già autorizzata conserva la propria approvazione indipendente; i riferimenti e i rilievi della revisione annullata vengono rimossi.

La pagina Suggerimenti non invia testi a servizi esterni. Gli invii autorizzati sono documentati in [Analisi](analysis.md): la cancellazione locale non dimostra la cancellazione presso il fornitore.

## Archivi e transazioni

`dictionary_versions` conserva la definizione completa di ogni revisione dal primo avvio con C2, compresa la configurazione presente all'aggiornamento. Le revisioni B2 anteriori all'aggiornamento che non erano state archiviate rimangono indisponibili, senza ricostruzioni inventate.

`suggestion_publications` conserva l'insieme completo delle definizioni approvate, la versione precedente, azione, data, revisione del dizionario verificata e versione del catalogo incorporato. `suggestion_catalogs` archivia la definizione incorporata. La migrazione aggiuntiva dello storico ha una propria versione e avviene nella transazione della migrazione B1; gli archivi del dizionario vengono inizializzati nella transazione di migrazione del dizionario. Schema incompleto o versione futura generano un errore, senza sostituire i dati.

Nel percorso manuale la validazione del processore e il calcolo dei conflitti avvengono fuori dai lock di scrittura. Il percorso automatico esegue gli stessi controlli dentro la transazione immediata di ingestione e pubblicazione, sul dizionario allegato corrente. La pubblicazione rilegge sotto transazione immediata versione delle proposte, versione pubblicata, revisione del dizionario e validità corrente delle evidenze. Approvazione, pubblicazione, puntatore corrente e audit diventano visibili insieme. Il dizionario è allegato alla connessione SQLite per una lettura coerente e non riceve scritture dalla pubblicazione. Non è richiesta una transazione distribuita su due database scrivibili.

I metadati delle nuove sessioni conservano `dictionary-v2:<revisione>;suggestions-v1:<pubblicazione>` e `cleanup-review-v1:<pubblicazione>`, oppure la versione incorporata quando la pubblicazione è zero. `SuggestionStore::archived_session_snapshot` ricostruisce una combinazione archiviata senza attivarla.

## API per D1

Il modulo pubblico Rust `voiceflow_desktop::suggestions` espone `SuggestionStore::open(history_path, dictionary_path)`, `IngestRequest`, `Candidate`, `Definition`, `EvidenceRef` e `ingest(request) -> Result<ReviewSnapshot>`. La pagina usa soltanto gli IPC `suggestions_snapshot` e `suggestions_review`; non esiste un IPC di ingestione generica o approvazione automatica.

Lo schema JSON è [suggestion-ingest-v1.json](schemas/suggestion-ingest-v1.json). I tipi Rust negano campi sconosciuti. Lo schema guida l'output strutturato; il backend verifica inoltre esistenza e appartenenza degli ID, byte/lunghezze, operation whitelist, capacità di sette parole, esempi e gruppi effettivi. Il compilatore rimane l'autorità finale. Non sono accettati conteggi, frequenze, flag di conferma umana, stato approvato o versioni scelti dall'analizzatore.

Una richiesta contiene:

- `candidate.definition`: `category: "terminology"` con `preferred`, `variants`, `matching`, `context`; oppure `category: "cleanup"` con `operation` C1 e `context`.
- `candidate.language`: `*`, `it` o `en`; `groupIds`: ID esistenti del dizionario. I gruppi sono selezionati manualmente per la sessione.
- `candidate.reason`, `positiveExamples`, `negativeExamples`. I controlli hanno input e output uguali; gli esempi positivi devono mostrare una modifica. Tutti passano il processore isolato prima dell'ingestione.
- `evidence`: 1–64 riferimenti. Ogni riferimento ha `sessionId`, `revisionId`, `originalIndex`. `revisionId` identifica una revisione della stessa sessione, ricordata e non annullata. `originalIndex` è l'indice zero-based di un segmento ASR salvato. Entrambi null indicano il risultato elaborato. Non possono essere valorizzati insieme.

Un'evidenza deve contenere l'input di almeno un esempio positivo; una correzione umana deve contenere anche il relativo output. Le riscritture non sono scomposte dal backend in sostituzioni. L'analizzatore propone esplicitamente una regola circoscritta, che resta un'ipotesi fino all'approvazione manuale o al criterio automatico esplicitamente abilitato nell'app.

Identità della proposta: definizione normalizzata, lingua e gruppi ordinati. Le varianti e le eccezioni sono ordinate e deduplicate. Una proposta uguale unisce riferimenti reali senza cambiare silenziosamente motivazione, esempi o definizione già revisionati. I nuovi riferimenti devono sostenere gli esempi della proposta esistente. Se l'analizzatore vuole cambiare la definizione deve produrre una proposta diversa; errori di ingestione non attivano niente. D1 deve decidere come presentare i lotti parzialmente invalidi; ogni chiamata `ingest` è atomica.

`ReviewSnapshot.proposals` espone stati `proposed`, `deferred`, `rejected`, `approved`, `disabled`. `approvedAt` traccia l'autorizzazione indipendentemente dallo stato attivo; `evidence.humanConfirmed` viene calcolato dallo storico. Le pubblicazioni espongono gli ID effettivi delle regole attive, inclusi i ripristini. D1 deve leggere questi stati e non trasformare output automatici in esempi umani.

`review` richiede `id`, `expectedRevision`, `expectedVersion`, `dictionaryVersion`, `requestKey`, `action`. `restore` usa `id: null` e `expectedRevision: null`. La stessa chiave con lo stesso payload restituisce lo stato attuale senza una seconda pubblicazione; la stessa chiave con un payload diverso è rifiutata. Dopo conflitto di versione serve rileggere e confermare nuovamente la decisione nella UI. D1 deve chiamare solo `ingest`, mai `review` per pubblicare automaticamente.

## Ingestione dei lotti D1

L'analizzatore usa [analysis-output-v1.json](schemas/analysis-output-v1.json), un envelope limitato a 16 candidati del contratto sopra. Il percorso interno `ingest_prepared` aggiunge, nella stessa transazione di ogni candidato, consenso attuale, versione privacy, appartenenza esatta al lotto e fingerprint delle fonti. Una sessione esistente ma non preparata non è un'evidenza ammessa. La riuscita restituita al worker corrisponde al commit dell'ingestione, senza dipendere da una successiva lettura dell'intera pagina di revisione.

Output incompleto o invalido blocca l'intero lotto; candidati individuali invalidi in un envelope completo producono uno stato parziale e un conteggio esplicito. La policy di checkpoint, privacy e ripresa è descritta in [analysis.md](analysis.md). L'API pubblica C2 `ingest` e gli IPC di revisione mantengono il loro contratto.

## Approvazione per confidenza

Il criterio nelle Impostazioni è inizialmente disattivato. Quando attivo, soltanto
nuovi rilievi con confidenza intera superiore a 90 possono essere pubblicati dopo
validazione, controlli delle fonti e dei conflitti. La confidenza è una stima del
provider sull'azione, non accuratezza misurata. Le fasce 75–90 alta, 50–74 media,
0–49 bassa e i punteggi sconosciuti richiedono revisione manuale.

Rifiuti, rinvii, modifiche, disattivazioni e ripristini manuali impediscono successive
attivazioni automatiche della stessa definizione, anche quando una nuova fonte
riapre la proposta. Le ipotesi precedenti senza punteggio non vengono convertite in
regole automatiche. Gli esiti storici restano consultabili per tentativo in Analisi;
`auto-approve:<id>` identifica le pubblicazioni automatiche. I rilievi di mantenimento
non creano proposte vuote né nuove versioni. Le regole pubblicate valgono dalla
prossima sessione e mantengono i normali comandi Disattiva e Ripristina.
