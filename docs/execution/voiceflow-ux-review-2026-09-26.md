# Esecuzione del piano UX

Piano approvato il 26 settembre 2026. Stato: COMPLETE.

## Baseline confermata

Branch `codex/tauri-streaming-app`, HEAD `0688bd8f52587ada388f70a70cafe94be4967a85`.
GitButler non inizializzato. Le 14 modifiche tracciate preesistenti coincidono
con la baseline del piano: 746 aggiunte e 58 rimozioni, SHA-256 del diff
`012ae9d5bd0b8f881e0784b8e92937c3275354fca19141ae22c05098fc3a7a96`.
Copia dei 212 file tracciati e non ignorati, manifest SHA-256 e patch iniziale
in `/tmp/voiceflow-ux-20260926-baseline`. Il percorso è una copia di verifica,
non un worktree. Nessun commit previsto.

Baseline rieseguita: `cargo test --workspace --locked --quiet`, 200 test superati,
5 ignorati già presenti; `git diff --check` superato.

## Avanzamento

| Lotto | Stato | Verifica |
| --- | --- | --- |
| 1. Dizionario | APPROVED | Verifica indipendente, build firmata e caricamento WebView reale superati |
| 2. Avvio manuale e metadati | APPROVED | Riparazione approvata da nuovo revisore; smoke helper nativo superato |
| 3. Elenco e dettaglio Analisi | APPROVED | Verifica indipendente sulla sorgente finale, UI9 + prove avverse |
| 4. Storico | APPROVED | Nuova verifica indipendente: focus, bozze e regressioni condivise superati |
| 5. Impostazioni e verifica integrata | APPROVED | Revisione indipendente, 70 gruppi UI, 208 test Rust, build firmata e app installata verificati |

La verifica UI usa asset compilati e IPC simulato con richieste intercettate,
senza listener. Le prove native macOS e dell'helper sono riportate separatamente
nel controllo finale. Nessuna analisi remota su dati personali è stata eseguita.

## Lotto 1: Dizionario

Ricerca nelle varianti, ordinamento stabile e pagine da 10 righe; gestione
gruppi separata dalla selezione attiva. Editor con opzioni avanzate raccolte,
azioni persistenti, protezione delle bozze e ripristino del focus. Eliminazione
gruppo con conteggio dei termini conservati e di quelli che diventano globali.
Backend e semantica delle regole invariati.

Typecheck, build UI e 11 gruppi di prove sul browser superati. Il revisore
indipendente ha aggiunto prove avverse su ordinamento con nomi uguali, chiusura
esterna, errori di cancellazione, doppio invio e navigazione con bozze aperte.
Otto cicli consecutivi Indietro/continua hanno conservato testo e focus.
Una prima prova del focus fallita durante l'aggiornamento degli asset non si è
riprodotta sulla build finale `index-CjdXN0SC.js`. Sei screenshot ispezionati,
inclusa la dimensione 780×620. Nessun errore browser o comando di dettatura.

Evidenze locali: `artifacts/dictionary-ux-batch1/result.json` e screenshot nella
stessa cartella. Build nativa registrata in
`/tmp/voiceflow-ux-batch1-native-build.log`.

`pnpm desktop:build` superato, firma stabile di app e helper verificata.
`pnpm desktop:verify-runtime` conferma che il processo attivo usa l'eseguibile
del bundle verificato. Nella WebView nativa verificati caricamento del Dizionario
tramite IPC reale, gruppi esistenti, controlli di ricerca/ordinamento, gestione
gruppi e paginazione vuota. Nessun dato personale modificato. CRUD e protezione
bozze sono esercitati dalle prove browser, non da scritture nel database personale.
La verifica nativa integrata delle altre schermate resta prevista al lotto 5.

## Lotto 2: avvio manuale e metadati

Modalità manuale propagata nello stesso launcher, executor e watchdog. Il comando
IPC trasferisce il lock esclusivo all'helper e conserva richiesta e collegamento
al tentativo. Migrazione additiva v4 con origine, modello, ragionamento e ID
dell'avvio; campi precedenti lasciati NULL. UI con dichiarazione dell'effetto
dell'autoapprovazione prima del clic e stati di richiesta/esecuzione/esito distinti.

Prima matrice: workspace 203 passati, 5 ignorati, poi ulteriori prove manuali e
di migrazione concorrente; fmt, clippy, TypeScript e build superati. Prove UI:
Analisi 9, impostazioni analisi 10, confidenza 4, pianificazione 5.

La verifica indipendente ha riprodotto un difetto preesistente incompatibile con
il piano: risposta `{ "candidates": [{}] }`, zero accettate e una rifiutata,
stato `partial` e `done=1`. Le risposte interamente invalide devono lasciare
le fonti riprendibili. Il lotto non è approvato finché la riparazione non supera
una nuova verifica indipendente.

Prova root sull'helper release reale con database temporaneo: manuale senza
consenso bloccato, manuale con consenso e archivio vuoto `no-work`, automatico
disabilitato `disabled`. Configurazione della pianificazione invariata; provider
fittizio mai avviato. Script riproducibile `/tmp/voiceflow-ux-native-helper-smoke.ts`.

Riparazione circoscritta: una risposta non vuota senza candidati validi termina
`failed/all-candidates-rejected`; risposta vuota valida `completed`, mista `partial`.
La riproduzione originale ora conserva `done=0`. Test multiprocesso automatici e
manuali coprono invalidità sintattica/semantica, checkpoint, backoff e retry dello
stesso lotto senza duplicare proposte già salvate. Workspace seriale: 205 passati,
5 ignorati. Clippy, fmt, TypeScript/build e prova UI del messaggio superati.

Da risolvere in QA-01: due fixture fallite sotto carico parallelo, poi passate
in sequenza. `every_item_lifecycle_rejects_tools_unknown_and_malformed_items`
ha ricevuto Timeout invece di Malformed; `controlled_clock_drives_actual_worker_deadline_dst_timezone_and_wake`
ha letto un pidfile creato ma ancora vuoto. Log della matrice seriale:
`/tmp/voiceflow-ux-batch2-repair-rust-serial.log`.

Per QA-01: `Fixture::pid` in `scheduler/tests.rs` attende soltanto l'esistenza
del file, poi legge/parsa una sola volta. Deve attendere contenuto valido con
deadline. La prova lifecycle usa una soglia di 2 secondi per ciascun processo
anche mentre altre fixture concorrenti avviano molti processi; distinguere
copertura del parser e deadline del provider senza indebolire la verifica.

Nuova verifica indipendente: APPROVED. Riproduttore originale e ulteriori
asserzioni confermano errore, `done=0` e backoff persistente che blocca il secondo
avvio. Scheduler 18/18; analisi 49 passati e 1 remoto ignorato; UI Analisi 9 e
impostazioni 11 superati. Screenshot desktop e 780×620 ispezionati. Copia della
baseline del prossimo lotto: `/tmp/voiceflow-ux-20260926-after-batch2`.

## Lotto 3: elenco e dettaglio Analisi

Tabella compatta e filtri globali SQLite per stato/origine. Route dedicate per
tentativi e lotti preparati, con filtri/pagina/selezione nell'URL. Dettaglio con
tentativi dello stesso lotto, prima/dopo, esiti storici e stato corrente delle
proposte. Fonti in sola lettura con riferimento alla revisione esatta, controllo
privacy dopo la lettura e rimando allo Storico per le modifiche.

Prima verifica SQLite indipendente: 50 passati e 1 test remoto ignorato, inclusi
filtri prima del limite su 25 record, 27 tentativi dello stesso lotto, valori NULL
precedenti, esclusione/cancellazione e risultati storici. Screenshot del dettaglio
a 780×620 e desktop ispezionati. Durante le prove è stata corretta la selezione
della riga al ritorno con Indietro; verifica della build aggiornata in corso.

Esito finale indipendente: APPROVED su `index-B5YODHco.js`, dopo correzione del
ritorno alla selezione e dello stato corrente delle proposte disattivate.
Quest'ultimo deriva dalla stessa pubblicazione attiva usata da Suggerimenti,
nella transazione di lettura del dettaglio; nessuna decisione storica modificata.
Nove gruppi UI e quattro prove avverse indipendenti superati: revisione esatta
anche con correzione successiva, esclusione durante lettura, errore di refresh
con fonte aperta e ID negativi/non numerici/non sicuri. Typecheck/build,
fmt/clippy workspace e quattro gruppi UI della confidenza superati.
Manifest finale `/tmp/voiceflow-batch3-final-sha256.txt`.

## Lotto 4: Storico

Lista compatta, dettaglio ampio con testo prioritario e confronto con elaborato
e originale, azioni persistenti. Ricorda, privacy, revisioni e metadati in sezioni
secondarie. La chiusura usa la protezione bozza già verificata nel Dizionario;
la segnalazione della bozza avviene anche nell'evento di modifica per coprire
modifica e uscita immediate. Nessuna modifica backend.

Typecheck/build e 11 gruppi UI Storico superati. Rerun dei flussi Dizionario e
Analisi superati. Screenshot desktop e 780×620 ispezionati, inclusi editor e lista.
Quattro file congelati nel manifest
`/tmp/voiceflow-ux-20260926-batch4-frozen/manifest.json`; prove e immagini in
`artifacts/history-ux-batch4`. Verifica indipendente in corso.

Prima verifica indipendente: NOT APPROVED per un problema di focus. Modifica,
clic sullo sfondo e "Continua a modificare" conservano la bozza ma lasciano il
focus su BODY anche dopo 30 secondi. Esc e X funzionano. Riparazione circoscritta
alla protezione condivisa, con regressioni sia Storico sia Dizionario.
Altre prove indipendenti superate: caricamento ritardato, refresh reale con testo
server cambiato, risposta persa dopo commit con retry idempotente, footer con
testo lungo e sezioni aperte a 780×620. Report in
`artifacts/history-ux-batch4/independent/result.json`.

Riparazione approvata da nuovo revisore: il guard conserva soltanto il focus
valido nel dialogo proprietario, scarta target disabilitati/scollegati e non
interferisce con il ritorno al pulsante di apertura dopo lo scarto confermato.
Hash congelati invariati. Riproduttore rigoroso, Storico 13, Dizionario 14 e
typecheck passati. Ulteriori cicli indipendenti coprono Esc/X/sfondo, scarto e
riapertura dei gruppi senza richiamo tardivo del focus. Evidenze in
`/tmp/voiceflow-batch4-rereview` e `artifacts/history-ux-batch4/repair-result.json`.

## Lotto 5: Impostazioni e verifica integrata

Preferenze raccolte in Dettatura, Analisi, Automazione e Sistema. Navigazione
diretta fra sezioni, limiti e diagnostica raccolti, stato sano del sistema compatto.
Le azioni di recupero restano disponibili se la rimozione della pianificazione
fallisce dopo aver salvato la preferenza disattivata. Descrizione dell'avvio
manuale coerente con l'autoapprovazione salvata.

QA-01 introduce `pnpm test:ui`, una sola build e sette suite sequenziali con
Playwright fissato nel lockfile, integrate nella CI con upload degli artefatti.
Corrette entrambe le fixture intermittenti: attesa di un PID valido e distinzione
fra casi esaustivi del parser e prove del ciclo di vita tramite processi reali.
Smoke dell'helper su SQLite temporaneo, con ambiente minimo e provider sentinella.

Sorgente congelata in `/tmp/voiceflow-batch5-frozen-manifest.json`.
Matrice finale dell'implementazione: TypeScript/build, 70 gruppi UI, 208 test Rust
con 5 integrazioni già ignorate, fmt/clippy, 4 prove firma, 3 prove runtime e smoke
helper superati. Screenshot delle quattro sezioni a 1060 e 780 pixel ispezionati.
Evidenze in `artifacts/ui/result.json`, `artifacts/native-helper/result.json` e
`/tmp/voiceflow-batch5-ui-final.log`.

Verdetto indipendente finale: APPROVED, nessun rilievo bloccante. Ripetuti con
successo workspace Rust parallelo, fmt/clippy, TypeScript/build, tutti i 70 gruppi
UI e i 7 test degli script. Tre prove aggiuntive coprono rimozione pianificazione
fallita con bozza divergente, conservazione dei cinque limiti dopo errore di
salvataggio e blocco dei controlli nelle fasi attive della dettatura. Tutti i 26
hash congelati coincidono; preservate le modifiche preesistenti. Report in
`/tmp/voiceflow-batch5-independent-final-report.json`.

## Controllo finale nativo

`pnpm desktop:build` superato. Build e helper firmati con l'identità stabile
esistente; log `/tmp/voiceflow-ux-final-native-build.log`. App aggiornata in
`/Applications/VoiceFlow.app`, firma profonda e rigorosa verificata. Il controllo
runtime conferma che il processo aperto usa l'eseguibile installato e verificato.
SHA-256 dell'eseguibile, uguale al bundle prodotto:
`bd3c90095da763bea1b51c0c82e84b169e4fc9bcdf6831d4c8e905223dab90c0`.

Smoke ripetuto sull'helper effettivamente installato: manuale senza consenso
`consent-unavailable`, manuale con archivio vuoto `no-work`, automatico
disattivato `disabled`. Zero tentativi, provider mai chiamato, configurazione
temporanea invariata. Risultati in `artifacts/native-helper/result.json`.

WebView nativa e IPC reale verificati: quattro sezioni Impostazioni con valori
salvati preservati, navigazione a Sistema e diagnostica espandibile; Analisi
vuota con filtri e dichiarazione dell'autoapprovazione prima dell'avvio;
Dizionario con gruppi locali, ricerca e paginazione; Storico con caricamento del
testo, confronto e azioni; Suggerimenti e pubblicazioni. Bozza temporanea del
Dizionario conservata dopo Escape e Continua a modificare, con focus sull'editor,
poi scartata senza salvare. Storico aperto e chiuso senza modifiche.

File LaunchAgent invariato, SHA-256
`ec9a5762c2ee9ad1af08f52a4f6f493c991134b626658570863fd9288faae216`.
Nessuna modifica ai testi personali, ai consensi o alla pianificazione. Nessun
avvio del microfono o del provider reale. Dettagli Analisi con risultati e CRUD
persistenti coperti da fixture isolate, non da scritture nei dati personali.
I 5 test d'integrazione ignorati restano tali; la CI è aggiornata ma non è stata
eseguita su un servizio remoto. App locale firmata, non notarizzata per distribuzione.
Controllo finale `git diff --check` superato. Nessun commit o push.
