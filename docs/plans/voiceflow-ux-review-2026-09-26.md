# Revisione UX e avvio manuale dell'analisi

Stato: COMPLETE. Piano approvato dall'utente il 26 settembre 2026 con
"procedi". Esecuzione dei cinque lotti nell'ordine previsto, con verifica
indipendente e riparazione prima del passaggio al lotto successivo.

Registro di esecuzione: [voiceflow-ux-review-2026-09-26](../execution/voiceflow-ux-review-2026-09-26.md).

## Piano essenziale

| Ordine | Intervento | Risultato atteso |
| --- | --- | --- |
| 1 | Dizionario | CRUD coerenti per termini e gruppi; ricerca anche nelle varianti; ordinamento e paginazione; editor essenziale con opzioni avanzate; protezione delle modifiche non salvate. |
| 2 | Avvio manuale dell'analisi | Pulsante "Esegui analisi ora" che avvia lo stesso lavoro della pianificazione, anche fuori fascia, un lotto per clic. Stessi consensi, limiti, checkpoint e controlli contro i duplicati. |
| 3 | Elenco e dettaglio Analisi | Tabella compatta e filtrabile, rimozione del pannello iniziale superfluo, pagina dedicata a ciascuna esecuzione con fonti, rilievi, prima/dopo, decisioni, errori e tentativi. Modello e ragionamento registrati per i nuovi tentativi. |
| 4 | Storico | Testo e correzione in primo piano, confronto leggibile, azioni accessibili; revisioni, privacy e metadati in secondo piano. |
| 5 | Impostazioni e verifica finale | Sezioni Dettatura, Analisi, Automazione e Sistema; rimozione di testi ripetitivi; diagnostica e limiti in Avanzate; verifica dei flussi integrati. |

Le trascrizioni elaborate restano nello storico ed escono dalla coda pendente.
Errori tecnici e interruzioni restano riprendibili. Un risultato valido con
alcune proposte scartate viene mostrato come "Completata con avvisi" e segue
i checkpoint attuali. Le nuove correzioni ricordate possono generare nuovo lavoro.

La logica dei Suggerimenti rimane quella confermata: regole validate, revisione
manuale o approvazione automatica opzionale sopra 90, versioni disattivabili e
ripristinabili. L'analisi manuale usa la stessa preferenza di autoapprovazione.

Ogni lotto viene implementato e poi verificato in un contesto indipendente.
Si passa al successivo dopo la verifica e le eventuali correzioni. L'approvazione
del piano autorizza questa sequenza senza ulteriori conferme tra i lotti,
salvo nuove decisioni che cambino il perimetro concordato.

Criteri di completamento: flussi CRUD e navigazione verificati, test mirati e integrati
superati, controllo visivo delle schermate renderizzate e verifica nativa delle
parti macOS coinvolte. Qualsiasi prova non eseguita resta esplicitamente pendente.

Esclusi: aggiornamenti automatici, gestione degli appunti, nuove statistiche,
backup/esportazione e word boosting. Non sono necessari a questi interventi.

## Specifiche di esecuzione

Le sezioni seguenti conservano evidenze, contratti e verifiche per lo sviluppo.

## Perimetro

Richiesta: rendere coerenti i CRUD del Dizionario, migliorare lo Storico,
aggiungere l'avvio manuale dello stesso lavoro dell'analisi pianificata,
rifare elenco e dettaglio Analisi e ridurre rumore e informazioni tecniche
nelle Impostazioni. L'utente ha confermato questi interventi e il funzionamento
attuale dei Suggerimenti, chiedendo il piano finale prima dello sviluppo.
La logica dei Suggerimenti va preservata e verificata nelle nuove pagine Analisi.

## Baseline

- Repository: `/Users/davide/Desktop/VoiceFlow`.
- Branch: `codex/tauri-streaming-app`.
- HEAD: `0688bd8f52587ada388f70a70cafe94be4967a85`.
- GitButler non inizializzato, rilevato tramite `but status --json`.
- Diff preesistente: 14 file tracciati, 746 aggiunte e 58 rimozioni; file non
  tracciato `docs/verification/analysis-model-reasoning.md`.
- SHA-256 del diff tracciato iniziale:
  `012ae9d5bd0b8f881e0784b8e92937c3275354fca19141ae22c05098fc3a7a96`.
- Le modifiche preesistenti ad analisi, provider, schema, preferenze SQLite,
  documentazione e verifiche costituiscono la baseline da preservare.
- Stack esistente: React, TypeScript, TanStack Router, shadcn radix-nova,
  Tauri/Rust, SQLite. Comandi JS con pnpm; Node previsto dal progetto: 24.
- `pnpm desktop:check`: superato.
- `cargo test --workspace --locked --quiet`: 200 test superati, 5 ignorati,
  nessun fallimento. I test ignorati richiedono integrazioni o fixture esplicite.
- La diagnosi UX deriva dal codice dei percorsi effettivi. Non è una verifica
  visiva della build nativa corrente.

Non creare worktree, non avviare server, non modificare lo storico personale,
non attivare il job o inviare dati remoti durante la preparazione del piano.

## Evidenze e interventi

| ID | Priorità | Evidenza e causa | Intervento |
| --- | --- | --- | --- |
| UX-01 | Alta | `routes/dictionary/index.tsx`: ricerca solo su `item.text`, tutte le righe tramite `terms.map`, azioni del gruppo condizionate a `selectedGroup`. Gestione gruppi, filtro e attivazione convivono nello stesso blocco. | Separare gestione e attivazione gruppi; ricerca su termini e varianti; ordinamento e paginazione; azioni esplicite e coerenti. |
| UX-02 | Alta | `components/dictionary/term-dialog.tsx`: formulario lungo con tutte le condizioni esposte; chiusura consentita quando non occupato, senza protezione della bozza. `delete-dialog.tsx` documenta che eliminare l'ultimo gruppo rende i termini globali. | Editor con grafia, varianti e gruppi subito visibili; lingua e condizioni avanzate raccolte; protezione modifiche non salvate; spiegazione concreta dell'effetto dell'eliminazione. |
| UX-03 | Alta | `scheduler/runtime.rs`: fascia, consenso e disponibilità ricontrollati dal launcher, executor e watchdog. Non esiste un comando UI di esecuzione. Avviare semplicemente il job fuori fascia non esegue l'analisi. | Modalità manuale esplicita nello stesso helper e motore, propagata a tutti i controllori; nessuna riscrittura temporanea della pianificazione. |
| UX-04 | Alta | `routes/analysis/index.tsx`: selezione in `useState`, dettaglio sotto l'elenco, card per ciascun tentativo; `router.ts` registra solo `/analysis`. | Elenco compatto con filtri e route dinamica per il dettaglio, apribile direttamente e compatibile con Indietro. |
| UX-05 | Media | `analysis/runs.rs` e schema tentativi: tempi e conteggi disponibili; modello, ragionamento e origine manuale/automatica non fanno parte del record storico del tentativo. | Salvare questi metadati all'avvio dei nuovi tentativi. Per i vecchi mostrare non disponibile; mai usare le impostazioni correnti come prova della configurazione passata. |
| UX-06 | Alta | `components/history/history-editor.tsx`: un unico dialogo scorrevole contiene editor, azioni, conferma, privacy, originali, revisioni e metadati. | Testo e correzione in primo piano, confronto accessibile, revisioni e metadati secondari; azioni persistenti e feedback vicino all'azione. |
| UX-07 | Media | `routes/settings/index.tsx`, `analysis-settings.tsx`, `scheduler-settings.tsx`: spiegazioni ripetute, badge tecnici e dettagli di implementazione sempre esposti. | Impostazioni raccolte per compito; diagnostica e limiti avanzati espandibili; avvisi visibili soltanto quando richiedono intervento. |
| QA-01 | Alta | CI esegue typecheck, build, firma e test Rust; gli script UI separati richiedono `PLAYWRIGHT_MODULE` e non compaiono nel workflow. | Rendere riproducibili e integrare le regressioni dei flussi modificati. Le prove simulate non sostituiscono helper, WebView e integrazione macOS reali. |

La precedente panoramica indicava erroneamente la paginazione del Dizionario:
non esiste nella versione esaminata. I comandi CRUD esistono nel backend;
questa constatazione non dimostra una buona esperienza dei flussi UI.

## Decisione di prodotto: Esegui ora

L'utente ha chiarito che vuole eseguire la procedura reale della pianificazione,
con le stesse regole per fonti completate ed errori. Proposta: analisi reale di
un solo lotto di fonti pendenti, utilizzabile fuori fascia e senza abilitare la
pianificazione automatica. Il pulsante principale appartiene ad Analisi.

- Richiede il consenso OpenAI già attivo e l'helper disponibile.
- Riusa provider, selezione fonti, esclusioni, budget, lock, cancellazione,
  priorità della dettatura, deduplicazione e checkpoint del job pianificato.
- La modalità manuale deroga solo ad attivazione e fascia della pianificazione.
  Mantiene timeout, limiti giornalieri e attesa dopo errori. Non riporta a zero
  i checkpoint e non forza la rianalisi di una sessione già completata.
- Un clic avvia al massimo un lotto. Doppio clic e concorrenza con launchd
  non producono due provider. Nessun ciclo manuale di retry automatico.
- Usa la stessa preferenza di approvazione automatica: può quindi pubblicare
  regole se autorizzato. Presentare questo effetto prima dell'avvio.
- Rende distinguibili: richiesta di avvio ricevuta, esecuzione partita,
  nessuna fonte preparabile, blocco e analisi conclusa. Un processo avviato
  non equivale a un'analisi riuscita.
- Registra origine manuale/automatica e collega l'esecuzione al suo dettaglio.
  Un avvio senza lotto resta nella diagnostica con il motivo.
- Nessuna modifica della fascia salvata o della configurazione LaunchAgent.

Procedura comune: una coda persistente con checkpoint per fonte, lock esclusivo
del worker e importazione idempotente. Una fonte è un originale oppure una
correzione ricordata; una successiva correzione può costituire nuovo lavoro.
La trascrizione resta nello storico e viene rimossa soltanto dalla coda pendente.

| Esito | Stato delle fonti |
| --- | --- |
| Completata, anche senza suggerimenti | Fonti ancora valide e immutate marcate `done=1`. |
| Risposta valida con alcune proposte scartate | Stato `partial`, proposte valide salvate, fonti ancora valide marcate completate. È già il comportamento corrente; esporlo come "Completata con avvisi" e non come errore tecnico da riprovare. |
| Errore del provider, timeout, risposta interamente invalida, errore di archiviazione | Nessun avanzamento dei checkpoint; ripresa con gli stessi limiti. |
| Interruzione, crash o cancellazione | Nessun avanzamento dei checkpoint; recupero solo dopo aver verificato l'assenza del precedente worker. |
| Fonte modificata, esclusa o eliminata durante il lavoro | Ricontrollare validità e fingerprint; non marcare come analizzata una nuova versione. |

Le proposte già salvate prima di un errore non vengono duplicate alla ripresa.
Il retry può ripetere una richiesta remota interrotta, ma non deve ripetere i
suoi effetti locali. Non promettere esecuzione remota "esattamente una volta".

Il pulsante non esegue una dimostrazione con frasi sintetiche. Le fixture
restano strumenti di verifica. Il clic manuale verifica il lavoro eseguito;
non prova che launchd scatti all'ora prevista.

## Disegno delle schermate

### Dizionario

Tabella di termini con grafia preferita, varianti, gruppi, lingua e indicazione
di conflitto o assenza di varianti. Ricerca comprende le varianti. Ordinamento
stabile, filtro per gruppo e paginazione con conteggi corretti dopo le modifiche.
Gestione gruppi accessibile senza dover prima selezionare un filtro. Attivazione
per la prossima dettatura distinta e compatta.

Editor con campi essenziali in apertura e condizioni avanzate espandibili.
Errori presso i campi, bozza conservata in caso di errore, protezione alla
chiusura con modifiche, conferma di salvataggio e ritorno al contesto della lista.
I termini senza varianti restano consentiti, ma dichiarati come promemoria.
La cancellazione di un gruppo mostra quanti termini restano e quanti diventano
globali. Non cambiare implicitamente la semantica delle regole.

### Analisi: elenco

Titolo e azione Esegui ora. Eventuale stato sintetico soltanto se utile, per
esempio analisi in corso o impedimento all'avvio. Rimuovere il grande pannello
iniziale della pianificazione. Tabella con data, origine, esito, fonti,
risultati e durata; filtri per stato e origine applicati all'intero archivio,
non soltanto alla pagina caricata. Paginazione e selezione conservate nell'URL.
Gli avvii senza lavoro restano in una sezione diagnostica secondaria.

### Analisi: pagina di dettaglio

Route `/analysis/$batchId/attempts/$attemptId` e route separata per un lotto
preparato senza tentativi; cartelle `index.tsx`, come nelle preferenze del progetto.
Validare gli ID e gestire inesistenti, cancellazioni, caricamento ed errori.

Contenuti:

1. Esito, data, durata, origine, riepilogo del risultato e tentativi dello stesso lotto.
2. Rilievi con estratto, risultato consigliato, differenze, motivo, azione,
   confidenza ed esito effettivo: mantenuto, da revisionare o pubblicato.
3. Fonti collegate, distinguendo trascrizioni e correzioni; esclusioni e fonti
   eliminate rispettate anche dopo l'apertura della pagina.
4. Collegamenti ai suggerimenti e alle pubblicazioni coinvolte, distinguendo
   risultato storico del tentativo e stato corrente della proposta.
5. Errori comprensibili, motivo e prossimo intervento possibile.
6. Dettagli tecnici secondari: ID, byte, modello e ragionamento registrati,
   tempi osservati e conteggi. Nessun prompt o output grezzo aggiuntivo salvato.

Non inventare percentuali di avanzamento, token, costo, configurazioni storiche
o durate non misurate. Polling solo dove serve e nessuna risposta vecchia può
sovrascrivere la pagina successiva.

### Storico

Lista più compatta con testo, data, durata e azioni riconoscibili. Dettaglio
ampio con testo corretto prioritario, confronto con elaborato e originale,
cronologia secondaria e pulsanti sempre raggiungibili. Distinguere il semplice
salvataggio da una correzione ricordata per l'analisi. Esclusione della sessione
accessibile senza riempire il dialogo di spiegazioni tecniche.

### Impostazioni

Quattro gruppi: Dettatura, Analisi, Automazione e Sistema. Preferenze comuni
subito disponibili; limiti e diagnosi in Avanzate. Permessi e motore occupano
una riga quando regolari, con azioni evidenti se manca qualcosa. Terminologia
visibile orientata all'utente, per esempio "Analisi programmata" al posto di
"Attiva il LaunchAgent". Eliminare slogan, ripetizioni e note senza conseguenze
pratiche. Conservare brevi spiegazioni su invio remoto, autoapprovazione,
cancellazione e differenza fra salvataggio automatico ed esplicito.

## Lotti e criteri di accettazione

| Lotto | Ambito e dipendenze | Accettazione, rischio e ripristino |
| --- | --- | --- |
| 1 | UX-01/02, Dizionario. Primo intervento consigliato, indipendente dall'analisi remota. | Creare/modificare/eliminare termini e gruppi; ricerca per variante; paginazione dopo filtri e cancellazioni; bozza e focus corretti su errore; effetti eliminazione gruppo espliciti; sessione attiva invariata. Rischio medio, ripristino del solo diff UI e delle eventuali query additive. |
| 2 | UX-03/05, avvio manuale e metadati dei tentativi. | Stesso helper, avvio fuori fascia manuale, limiti e privacy invariati, doppio avvio impedito, priorità audio, revoca consenso, crash e nessuna fonte; migrazioni compatibili. Rischio alto, schema additivo senza ricostruzioni dei dati vecchi; non eliminare dati per tornare indietro. |
| 3 | UX-04, elenco e dettaglio Analisi, dipende da 2. | URL diretto e ricarica, Indietro, filtri globali, tentativi distinti, dati storici corretti, assenza fonti, polling senza race, esiti manuali visibili. Rischio medio, ripristino del diff UI/letture mantenendo i metadati additivi. |
| 4 | UX-06, Storico. | Copia, salvataggio, annullamento, conferma categorizzata, privacy e cancellazione preservati; differenze leggibili; nessuna perdita silenziosa di bozza. Rischio medio, nessuna migrazione necessaria salvo nuova evidenza. |
| 5 | UX-07 e QA-01, Impostazioni e verifica integrata. | Tutte le preferenze restano raggiungibili, errori azionabili, limiti avanzati conservati; regressioni dei cinque lotti riproducibili e integrate nella verifica ordinaria. Rischio medio, ripristino delle sole viste/configurazioni di test modificate. |

Ogni lotto: implementazione in un contesto dedicato, verifica indipendente in
un nuovo contesto, eventuale riparazione e nuova verifica. Prompt limitati ai
file e criteri del lotto, senza clonare tutta la conversazione. Nessun commit,
push, invio remoto o avvio di server è implicito nell'approvazione del piano.

## Verifica prevista

- Typecheck e build UI; test Rust mirati per comandi, query e migrazioni.
- Test UI con asset compilati e IPC simulato, senza listener, a 780×620 e
  dimensione desktop normale. Ispezione delle schermate renderizzate.
- Regressioni dei flussi effettivi, tastiera, focus, errori, doppio clic,
  navigazione, aggiornamenti concorrenti e dati mancanti.
- Alla chiusura integrata: Rust workspace, fmt/clippy, typecheck/build UI,
  test di firma e runtime pertinenti, controllo del diff rispetto alla baseline.
- Verifica nativa separata dell'helper e della nuova UI. Un'analisi reale con
  dati personali richiede autorizzazione concreta; le fixture non la simulano
  come già riuscita. Non avviare il microfono solo per provare una schermata.

## Funzioni spiegate ma escluse dai lotti

- Aggiornamenti app: rilevare, scaricare e installare nuove versioni firmate.
  Distinti dal download o aggiornamento dei modelli.
- Appunti: ripristino del contenuto precedente dopo l'incolla oppure rimozione
  dopo un intervallo, solo se gli appunti non sono cambiati nel frattempo.
- Calendario d'uso: attività e parole giornaliere, andamento settimanale.
- Distribuzione per app: richiede salvare l'app di destinazione, oggi assente.
- Tempo risparmiato: stima basata su velocità di digitazione scelta e durata
  della dettatura, dichiarata come stima; non misura il tempo di revisione.
- Strumenti futuri utili: backup/esportazione/ripristino, diagnostica esportabile
  priva di trascrizioni e credenziali, verifiche native riproducibili, distribuzione
  e aggiornamenti firmati se l'app viene distribuita ad altri utenti.

Il word boosting rimane un esperimento separato. Non è necessario portarlo
nel prodotto per completare questi flussi.
