# Rilievi con confidenza e approvazione locale

Implementazione del lotto dipendente del 26 settembre 2026. Verifica indipendente
e installazione della build finale restano affidate al coordinatore.

## Contratto e guardie

I nuovi rilievi richiedono nome, estratto esatto, motivazione, azione, confidenza
intera 0–100, candidato opzionale ed evidenze del lotto corrente. Mantenere il testo
salva una decisione senza regola. Rimozione e sostituzione richiedono rispettivamente
pulizia supportata e terminologia, esempi coerenti e controlli negativi invariati.

Le impostazioni precedenti migrano con `autoApproveHighConfidence: false`.
Soltanto i nuovi rilievi sopra 90 possono pubblicare. Il controllo locale verifica
consenso, fonti, tentativo, cancellazione, veti manuali persistenti e combinazione
corrente di dizionario e regole. Proposta, pubblicazione, audit ed esito vengono
committati insieme nella transazione SQLite immediata. Il dizionario allegato è
letto nella stessa transazione. Il provider non può invocare comandi di revisione.

Le identità precedenti senza punteggio rimangono manuali anche dopo ripetute
ripresentazioni con punteggio alto. I veti manuali conservano soltanto hash delle
identità normalizzate, comprese quelle precedenti alle modifiche. Le risposte
precedenti candidate/evidence usano il contratto di ingestione originale: nessun
nome, estratto o punteggio viene inventato retroattivamente.

La migrazione `analysis_schema` 3 aggiunge risultati per tentativo, riferimenti con
cancellazione delle copie strutturate quando qualsiasi fonte viene eliminata,
esclusa, dimenticata o annullata, e metadati locali per l'ammissibilità automatica.
Non conserva prompt né output grezzi. Le definizioni già approvate rimangono
indipendenti e disattivabili secondo la conservazione dichiarata nelle Impostazioni.

## Prove eseguite

Il test di caratterizzazione è stato aggiunto prima dell'implementazione e falliva
con 0 rilievi accettati invece di 1. Dodici test `confidence_` esercitano il percorso
reale `AnalysisStore::run` con provider fixture e SQLite temporaneo:

- Confini 0/49/50/74/75/90/91/100; impostazione assente o disattivata.
- Punteggi mancanti, negativi, oltre 100, frazionari, null e stringhe.
- Fonte inventata o estranea, azione incoerente, esempio negativo modificato e
  mantenimento che contraddice una correzione umana.
- Mantenimento senza proposta, eliminazione/esclusione e compatibilità legacy.
- Cancellazione nel controllo transazionale, revoca, mutazione della categoria
  ricordata, esclusione e modifica del criterio durante la risposta.
- Conflitti fra regole e dizionario cambiato durante il provider.
- Rifiuto, rinvio, modifica, disattivazione e ripristino manuali; nuova fonte e
  ripresentazione dell'identità anteriore a una modifica.
- Duplicati, rollback dell'intera pubblicazione e retry dopo un commit parziale;
  punteggio ed esito storici immutabili per tentativo.
- Snapshot della sessione precedente invariato e nuova versione alla sessione
  successiva. I test C2 esistenti conservano la prova del processore e della pausa.

La fixture `scripts/verify-confidence-ui.ts` carica gli asset di produzione con
Playwright e IPC Tauri intercettati, senza listener. I tre scenari verificano
impostazione esplicita, aggiornamento del tentativo in corso con tutti i confini,
testo/azione/risultato/fascia/esito, significato di stima, collegamento alla proposta,
punteggio della proposta e dati legacy sconosciuti. Screenshot dei valori 90 e 91
sono in `artifacts/confidence/`, incluso 90 alla larghezza minima di 780 pixel.
Sono state eseguite anche le fixture esistenti Analisi, storico analisi e Suggerimenti.
Il selettore del consenso nella prima fixture ora usa il nome accessibile, perché
la regione contiene due checkbox.

Comandi: `cargo test --workspace --locked`,
`cargo clippy --workspace --all-targets --locked -- -D warnings`,
`cargo fmt --all -- --check`, `git diff --check`, `pnpm desktop:check`,
`pnpm desktop:ui` e le quattro fixture UI. La build mantiene l'avviso già noto del
bundle oltre 500 kB. I test opt-in di integrazione/audio non sono stati abilitati.
Il run finale sequenziale `cargo test --workspace --locked -- --test-threads=1`
ha concluso con 192 passati: 57 core, 2 benchmark unitari, 133 desktop; 4 test
opt-in ignorati, nessun fallimento. Clippy, fmt, controllo diff, typecheck e build
sono passati. Le fixture UI hanno concluso 3 scenari confidenza, 7 analisi,
7 storico analisi e 7 suggerimenti.

Un precedente run parallelo ha fallito soltanto
`scheduler::tests::multiprocess_single_worker_capture_and_crash_cleanup`, alla
verifica `!c.recording()` in `scheduler/tests.rs:327`. Il test isolato è poi passato,
come l'intera suite sequenziale. Non sono stati modificati codice o test dello
scheduler per questo esito. Il revisore indipendente deve considerare questa
variabilità temporale. I log sono `artifacts/confidence/cargo-test-transient-failure.log`,
`cargo-test-final.log`, `scheduler-recheck.log` e `clippy.log`.

## Attivazione nativa successiva

Dopo la verifica indipendente e l'installazione autorizzata della build completa:
Impostazioni, Analisi con OpenAI, selezionare
`Approva automaticamente con confidenza superiore a 90`, quindi
`Salva preferenze analisi`. Conservare il consenso OpenAI e la fascia 02:30–06:00
già autorizzati. Verificare il valore riletto dalla UI prima della prova del job.
Il coordinatore esegue la prova nativa fuori fascia per ottenere una diagnostica
visibile di avvio saltato senza richiesta remota. Questa implementazione non ha
modificato l'app installata, il database dell'utente o il job launchd.

## Riparazione C1, C2 e C3 dopo la verifica indipendente

I quattro test di regressione iniziali fallivano sul codice precedente, come
registrato in `artifacts/confidence/repair-failing-before.log`. La riparazione
vincola l'input e l'output dell'esempio nominato a ogni correzione citata, anche
quando altri esempi del candidato sono validi. Il rifiuto lascia vuote le tabelle
di proposta, evidenza, rilievo, audit e pubblicazione.

Esclusione, eliminazione della fonte o revisione, dimenticanza e annullamento
rimuovono le copie non approvate e i riferimenti collegati. Una proposta con più
fonti viene rimossa interamente quando una fonte viene esclusa. Le definizioni
già approvate e le pubblicazioni restano indipendenti. I veti manuali persistono
come hash; la migrazione ripulisce anche le copie escluse conservate dal codice
precedente dopo avere recuperato i veti dall'audit. Lo storico operativo conserva
i conteggi e i collegamenti alle proposte eliminate diventano nulli.

Ogni nuovo rilievo conserva l'hash del candidato normalizzato. Lo snapshot della
proposta espone confidenza ed esito soltanto se il candidato corrente corrisponde
esattamente a quello valutato. Una modifica manuale mostra confidenza sconosciuta;
una successiva analisi coerente può fornire una nuova stima, rispettando il veto
manuale. I risultati storici non vengono riscritti. I rilievi precedenti senza
hash rimangono consultabili nello storico ma non attribuiscono una stima alla
proposta corrente.

Cinque nuovi test backend coprono i tre blocker e la migrazione. Il test esistente
sulle revisioni annullate ora verifica la rimozione della proposta non approvata
e il rifiuto dell'approvazione con snapshot obsoleto. Il run finale sequenziale
`cargo test --workspace --locked -- --test-threads=1` passa con 197 test, 4 opt-in
ignorati e nessun fallimento. Fmt, Clippy con `-D warnings`, controllo diff,
`pnpm desktop:check` e `pnpm desktop:ui` passano. Rimane l'avviso del bundle oltre
500 kB. Le fixture UI passano con 4 scenari confidenza e 7 ciascuno per analisi,
storico analisi e suggerimenti. Il nuovo scenario usa la modifica reale della
scheda con IPC intercettato, aggiorna la stima dopo una nuova analisi simulata e
verifica che l'azione precedente conservi testo e punteggio nello storico.

Il probe indipendente originale è stato ricompilato contro la libreria debug
aggiornata. Ora il caso multi-esempio è rifiutato, l'esclusione lascia zero rilievi
e zero proposte, e la proposta modificata ha `confidence=None` con risultato
storico `VoiceFlow` invariato. Passano anche i cinque casi di punteggio invalido
e la cancellazione delle copie di mantenimento. Fonte del probe invariata;
risultati in `artifacts/confidence-verification/probe-repair.log`.

Log della riparazione in `artifacts/confidence/`: `repair-focused.log`,
`repair-workspace-final.log`, `repair-clippy-final.log`, `repair-fmt.log`,
`repair-diff-check.log`, `repair-typecheck.log`, `repair-ui-build.log`,
`repair-confidence-ui-final.log`, `repair-analysis-ui.log`,
`repair-analysis-runs-ui.log`, `repair-suggestions-ui.log` e
`repair-probe-build.log`. Il primo run completo e il primo nuovo scenario UI
sono conservati nei log senza suffisso `final`; documentano rispettivamente
l'aspettativa privacy precedente e il reset della fixture durante il reload,
entrambi corretti prima dei run finali. La build firmata e la verifica indipendente
della riparazione restano al revisore successivo.

## Verifica indipendente e attivazione reale del 26 settembre

Seconda verifica indipendente: controlli automatici passati, nessun blocco residuo.
Riprodotti 197 test Rust passati e 4 opt-in ignorati, 17 test mirati confidenza,
fmt/clippy/typecheck/build UI, fixture 4/7/7/7, firma 4/runtime 3 e manifest 4/4.
I probe originali C1/C2/C3 e ulteriori prove positive, di migrazione e modifica
sono passati. Verbale completo locale: artifacts/confidence-reverification/REPORT.md.

Build firmata installata in /Applications/VoiceFlow.app e avviata, dopo verifica
della dettatura ferma. Vecchio bundle conservato negli artefatti locali di backup.
Firma deep/strict dell'app e strict del helper valide; identità e requirement
stabili. CDHash app 36231eb0b4429101df9b74376606766765e1e957,
helper 9a611eca966ec35a5c37a823b6f65500e3db12ec. Processo avviato dal bundle
installato, PID 37472 osservato; navigazione Analisi presente nella WebView nativa.

Su autorizzazione esplicita dell'utente, selezionata e salvata nell'app la politica
sopra 90. Rilettura UI e SQLite in sola lettura confermano enabled=true e
autoApproveHighConfidence=true. Limiti conservati: 20 fonti per lotto, 65.536 byte,
2 richieste al giorno, 120 secondi per richiesta e 300 secondi al giorno.
Consenso OpenAI e LaunchAgent già attivi, fascia 02:30–06:00 invariata.

Prova reale launchd alle 16:22:24 CEST: kickstart del job installato, un avvio,
uscita 0 e riga scheduler con reason=outside-window, fine registrata e nessun
batch. La pagina Analisi mostra Fuori dalla fascia oraria con l'orario corretto,
job caricato, worker inattivo e fascia attesa. Conteggio tentativi provider prima
e dopo pari a zero. Nessun testo inviato; questo prova avvio, rispetto della fascia
e consegna della diagnostica alla UI, non il successo di un'analisi OpenAI.

La prima esecuzione remota notturna e i suoi rilievi reali restano da osservare.
Il flusso dei punteggi/pubblicazioni è verificato con SQLite e provider fixture;
nessun risultato finto è stato importato nello storico personale. Le precedenti
prove audio/manuali del piano v1 restano separate e pendenti.
