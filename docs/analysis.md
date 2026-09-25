# Analisi locale dei lotti con OpenAI

L'analisi è disattivata inizialmente. In Impostazioni, `Analisi con OpenAI` richiede la selezione esplicita del consenso e il salvataggio delle preferenze. La dettatura resta locale. Il consenso riguarda l'invio a OpenAI degli originali ASR, dei risultati elaborati, delle correzioni ricordate e delle definizioni pertinenti selezionati per il lotto. Le nuove regole richiedono sempre l'approvazione nella pagina Suggerimenti.

Nel dettaglio di ogni trascrizione, `Escludi questa sessione dall'analisi con OpenAI` esclude anche le sue correzioni. L'esclusione e la disattivazione invalidano i lotti pendenti e interrompono quelli in corso tramite il contratto di cancellazione. La verifica dentro l'ingestione impedisce che una risposta arrivata dopo la revoca aggiunga nuove proposte. Un invio già accettato da OpenAI può comunque essere elaborato. VoiceFlow non dichiara di cancellare dati ricevuti dal fornitore o di annullarne il costo.

## Preparazione e limiti

Una fonte è una trascrizione oppure una revisione ricordata. Il checkpoint della trascrizione non consuma quello di una correzione ricordata in seguito: anche una vecchia sessione può quindi produrre un nuovo lotto. Le modifiche non ricordate non diventano conferme umane. Un cambio di categoria della correzione la rende nuovamente pendente; ripetere la stessa conferma non genera lavoro duplicato.

I limiti configurabili sono 1–100 fonti per lotto, 4.096–262.144 byte per richiesta, 1–20 tentativi al giorno, 1–600 secondi per richiesta e 1–3.600 secondi al giorno. I default sono 20 fonti, 65.536 byte, 2 tentativi, 120 secondi per richiesta e 300 secondi al giorno. Il conteggio del giorno usa UTC, distinto dalla finestra locale che appartiene allo scheduler D2. La dimensione comprende istruzioni, metadati e contesto serializzato. Le fonti troppo grandi restano pendenti senza troncamenti; aumentare il limite o escludere la sessione.

L'analizzatore cerca terminologia e famiglie circoscritte di false partenze, ripetizioni ed elisioni. Il prompt e lo schema richiedono condizioni, eccezioni ed esempi di controllo. Il backend accetta soltanto le operazioni C1 e i riferimenti esatti del lotto. C2 verifica gli esempi su parole complete, il compilatore, i gruppi e l'esistenza delle correzioni umane.

Il contesto comprende proposte pertinenti, stato del rifiuto e definizioni approvate che compaiono nelle nuove fonti. Può includere una regola del dizionario manuale quando il suo risultato compare nel testo che l'utente corregge. Sono omessi gli esempi e le motivazioni storiche delle proposte: non vengono reinviati testi di sessioni estranee. Una proposta collegata a una sessione esclusa viene esclusa interamente dal contesto. Le definizioni esplicitamente approvate restano dati indipendenti dopo la cancellazione della fonte, come già previsto da C2; possono essere disattivate o rimosse dai flussi di revisione. Una definizione non è una conferma umana e non autorizza riferimenti fuori dal lotto.

## Stato persistente e privacy

Le tabelle `analysis_*` risiedono nello storico SQLite, con una versione di schema indipendente e migrazione transazionale. Non modificano l'archivio delle impostazioni di dettatura. Conservano consenso, esclusioni, ID delle fonti, fingerprint, stati, tentativi e contatori. Non conservano payload, prompt, risposte del modello, stderr o copie testuali delle evidenze. Il payload viene ricostruito in RAM. Eliminare una sessione elimina anche i relativi eventi e riferimenti dei checkpoint tramite chiavi esterne.

Ogni tentativo viene registrato e riserva il timeout prima di chiamare il provider. I contatori comprendono anche errori, spawn falliti e timeout: non sono il numero di richieste accettate da OpenAI. I byte indicano il payload preparato per quei tentativi, non una ricevuta di consegna remota. Il tempo misurato è separato da quello riservato. Dopo un crash il tempo effettivo è sconosciuto e il budget conserva la prenotazione massima; non viene inventata una durata misurata.

Un errore dell'intero output non importa candidati e non avanza i checkpoint. Un output JSON completo può avere candidati invalidi: ciascun candidato è una transazione atomica separata, quelli validi vengono importati e il lotto termina `partial` con conteggi accettati/rifiutati. Anche questo lotto avanza le fonti ancora valide, evitando retry continui sugli stessi dati invalidi. Un retry dopo interruzione può ripresentare candidati già importati, ma C2 li deduplica e non riapre un rifiuto senza una nuova fonte identificabile. Nessun percorso di analisi chiama `review`, pubblica o attiva regole.

Un errore di archiviazione o infrastruttura durante l'ingestione interrompe invece il lotto come `failed`, con codice `ingest-storage`. Non incrementa i rifiuti semantici e non avanza alcun checkpoint del lotto. I candidati già committati restano disponibili, il tentativo resta nel budget e `prepare()` ripresenta lo stesso lotto per un retry entro i limiti. Il confine C2 distingue esplicitamente gli errori di validazione dagli altri errori, senza confrontare messaggi testuali. Se fallisce anche il salvataggio dell'esito, `run()` restituisce un errore; il tentativo può restare `running` e richiede `recover_interrupted()` dopo la verifica della prenotazione del worker. Anche in quel caso la transazione finale non consuma le fonti.

## Adattatore Codex CLI

Il contratto è stato verificato con `codex-cli 0.157.0`, `codex exec --help`, `codex features list` e la documentazione ufficiale [non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) e [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference), il 25 settembre 2026. Non è stata eseguita alcuna analisi remota durante lo sviluppo.

`Executables::discover()` cerca `codex` e `node` nel PATH, poi nelle installazioni Node 24 sotto `~/.nvm/versions/node`, quindi nei percorsi di installazione comuni. Non incorpora un percorso assoluto dipendente dalla versione. D2 può risolvere nuovamente i due eseguibili quando parte senza shell interattiva.

Il comando usa stdin, `--output-schema`, JSONL, `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, `--strict-config`, sandbox `read-only`, una directory temporanea privata estranea al repository e nessun file di output finale. L'autenticazione normale della CLI resta nel suo percorso originale; VoiceFlow non legge o copia credenziali. L'ambiente del processo conserva solo HOME/CODEX_HOME e il PATH necessario a Node e alle utility di sistema. Schema e percorsi di log/stato CLI sono nella directory temporanea, rimossa al termine. Il logging Rust e dei prompt è disabilitato e la cronologia CLI non viene conservata.

I controlli preventivi disattivano esplicitamente shell/unified exec, agenti, applicazioni, plugin, hook, scoperta skill, MCP, browser, computer use, generazione/lettura immagini, code mode, automazioni locali, dipendenze workspace, memoria, snapshot shell e altre capacità elencate in `provider.rs`. `tools.view_image=false`, `web_search="disabled"` e `project_doc_max_bytes=0` chiudono ulteriori percorsi di lettura. Le istruzioni trattano ogni stringa del payload come dato non attendibile.

La sandbox `read-only` limita le scritture degli strumenti; da sola non limita le letture a una cartella. La separazione dei dati forniti al modello dipende dai controlli preventivi delle capacità e dalla CLI installata. Il parser rifiuta anche eventi di esecuzione strumenti, ma questo controllo è successivo e non costituisce isolamento preventivo. I test verificano argomenti e processo sostitutivo, non il comportamento di una sessione remota reale. Un futuro cambiamento incompatibile dei flag o degli eventi deve fallire con un errore visibile; la CLI reale resta da verificare dopo il consenso nell'app.

Ogni stream stdout/stderr ha un tetto di 512 KiB. Timeout e cancellazione terminano il gruppo di processi, incluso il launcher Node e il figlio nativo. L'input viene scritto da un thread separato: un processo che non legge stdin non blocca la cancellazione. La risposta deve contenere un messaggio finale e `turn.completed`; JSON incompleto, errore, exit nonzero, eventi inattesi e oltre 16 candidati non importano nulla.

Per ogni evento `item.started`, `item.updated` e `item.completed`, il parser ammette soltanto `agent_message` e `reasoning` con testo stringa. Un item mancante, malformato, sconosciuto o relativo a strumenti invalida l'intera risposta, anche se seguito da un messaggio finale valido. Questo controllo resta successivo all'attività segnalata dalla CLI.

## API per il worker D2

`voiceflow_desktop::analysis` espone:

- `AnalysisStore::open(history_path, dictionary_path)`: apertura differita, stessa base dati dell'app.
- `status(now_ms)`, `configure(settings, consent)`, `excluded(id)`, `exclude(id, value)`: stato e preferenze persistenti. Salvare la stessa configurazione è idempotente.
- `prepare() -> Option<PreparedBatch>`: selezione locale limitata, ripresa del lotto pendente e capability opaca. `id()`, `source_count()` e `prompt()` sono disponibili al chiamante; i campi delle fonti non sono costruibili dall'esterno.
- `run(batch, &dyn Provider, &dyn Cancellation, now_ms) -> Result<BatchOutcome>`: un solo tentativo, senza retry automatici. Ricontrolla le fonti prima dell'invio e dentro ogni ingestione. Il provider viene chiamato fuori da ogni lock/transazione SQLite. Stato finale e contatori sono persistiti. `failed`/`ingest-storage` consente un retry tramite `prepare()`; un `Err` non autorizza a consumare le fonti o ad azzerare il budget.
- `recover_interrupted()`: da chiamare **solo dopo** che D2 ha verificato la scadenza della prenotazione del vecchio worker. Rende riprendibili i lotti interrotti e conserva il budget dei tentativi precedenti.
- `Provider`, `ProviderReply`, `ProviderError`: contratto sostituibile. Il provider restituisce dati e deve rispettare timeout/cancellazione; non riceve riferimenti ai database. `CodexProvider` implementa il processo limitato. I codici di errore persistiti non contengono output del fornitore.
- `Cancellation`: implementata da una closure `Send + Sync` o `AtomicBool`. D2 la collega alla finestra, alla scadenza e alla dettatura. La closure interna aggiunge la verifica di consenso/esclusioni.

D2 deve possedere una sola prenotazione del worker, chiamare queste funzioni fuori dal thread UI, verificare fascia/dettatura prima della preparazione e di ogni richiesta e fornire cancellazione durante le richieste. Non deve invocare ingestione generica o `review` sulle risposte. L'entrypoint interno `SuggestionStore::ingest_prepared` usa la stessa transazione di C2 con una guardia aggiuntiva per il lotto; il pubblico `ingest` mantiene il contratto precedente.

D1 registra solo IPC per preferenze, stato ed esclusioni. Non espone un IPC di esecuzione dell'analisi e non avvia job. Finestra 02:30–06:00, LaunchAgent, coordinamento con la dettatura e politica di retry appartengono a D2.

Lo schema completo della risposta è [analysis-output-v1.json](schemas/analysis-output-v1.json). Ogni elemento `candidates` usa il contratto C2. Il validatore locale applica anche i campi nullable obbligatori e i limiti dello schema prima della validazione semantica C2. Questo impedisce che il comportamento permissivo dei campi Rust `Option` accetti campi mancanti.

## Pianificazione disponibile in D2

Il helper, il LaunchAgent, la fascia locale e il coordinamento con la dettatura sono descritti in [Pianificazione locale dell'analisi](analysis-scheduler.md). L'attivazione richiede una seconda scelta esplicita nell'app, oltre al consenso D1. Il provider in quel processo usa il gruppo supervisionato del worker: una terminazione forzata conserva il budget riservato e rende necessario il recupero dei checkpoint dopo la prova del lock. Le directory CLI del worker vengono raccolte dal launcher o al successivo controllo sicuro dello stato. Il percorso D1 separato conserva la propria gestione del gruppo e dello scratch temporaneo.
