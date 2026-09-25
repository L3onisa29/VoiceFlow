# Verifica D2 / VF-07

Data: 25 settembre 2026. Implementazione sul branch `codex/tauri-streaming-app`, baseline del lotto `6c5f2aeed1182e64197cc82fba600c73c1f5cc73`.

Stato: implementazione e controlli automatici conclusi, pronta per verifica indipendente in sola lettura. Questo report non approva il lotto. Attivazione reale nell'app, prova nativa del LaunchAgent e build finale firmata restano pendenti. Non sono stati eseguiti job reali o chiamate OpenAI.

## Comportamento consegnato

Eseguibile separato `voiceflow-analysis`, packaging in `Contents/MacOS` e firma con la stessa identità stabile dell'app. Il LaunchAgent funziona senza finestra/app aperta, nella sessione dell'utente. Consenso OpenAI e attivazione scheduler restano separati e OFF inizialmente. Default locale 02:30–06:00, configurabile, anche 03:00–05:00 e fasce che passano la mezzanotte; durata limitata a 1–720 minuti. Nessun servizio web.

Installazione, aggiornamento, riparazione e disattivazione convergono dopo ripetizioni o fallimenti parziali. La revoca viene salvata prima di launchctl. Stato desiderato e rilevato sono distinti. Tutti gli IPC dello scheduler usano `spawn_blocking`, comprese attese di riconciliazione e comandi launchctl. Il processo di dettatura usa solo il coordinamento mediante lock e non dipende dal database scheduler o da launchctl.

I lock del kernel e i descrittori ereditati impediscono due worker, anche dopo una lease scaduta. Il gruppo executor/provider ha watchdog e controllo del launcher, pipe di liveness e cleanup prima del reap. Nessun PID persistito autorizza un kill. La cattura prende intenzione e permesso prima del decoder; li mantiene durante pausa/finalizzazione e li rilascia dopo teardown di capture e sessione. Il timeout conserva il budget D1 già prenotato; il recupero D1 avviene solo dopo prova di proprietà esclusiva.

Un avvio effettua al massimo un tentativo. Errori/interruzioni hanno almeno cinque minuti di backoff e massimo tre fallimenti al giorno UTC, oltre ai budget D1. I checkpoint impediscono doppie proposte nella ripresa e nell'ora autunnale ripetuta. Consenso, fascia e dettatura cancellano il lavoro. Nessuna analisi pubblica una regola.

La documentazione d'uso, le API, le fonti Apple e il protocollo manuale sono in [analysis-scheduler.md](../analysis-scheduler.md).

## Percorsi del lotto

- `Cargo.lock`, `apps/desktop/src-tauri/Cargo.toml`: dipendenze dirette libc e Chrono già presenti nel lock; MSRV workspace 1.88 invariata.
- `apps/desktop/src-tauri/src/bin/voiceflow-analysis.rs`: entrypoint dedicato.
- `apps/desktop/src-tauri/src/scheduler/{mod,coordination,runtime,tests}.rs`: configurazione SQLite, fascia, adattatore launchctl, plist, IPC, lock, processi, ripresa e test.
- `apps/desktop/src-tauri/src/analysis/provider.rs`: variante interna del provider per gruppo supervisionato, scratch nel contenitore del worker e terminazione dei discendenti. Il comportamento D1 separato resta coperto dai test precedenti.
- `apps/desktop/src-tauri/src/lib.rs`: modulo, stato e IPC scheduler. Il cambio preesistente a `desktop-settings.sqlite3` è esterno al lotto.
- `apps/desktop/src-tauri/src/service.rs`: permit di coordinamento prima dell'ASR, conservato nell'Active fino al teardown.
- `apps/desktop/src/lib/scheduler.ts`, `components/analysis/scheduler-settings.tsx`, `routes/settings/index.tsx`: controlli shadcn e schema IPC.
- `scripts/build-analysis-helper.ts`, `build-desktop.ts`, `build-signing.test.ts`: compilazione/helper nel bundle, firma dentro-fuori e test del contratto di packaging.
- `scripts/verify-scheduler-ui.ts`: prova React su asset compilati con IPC simulati e risorse intercettate da Playwright, senza listener.
- `docs/analysis-scheduler.md`, appendice `docs/analysis.md`, questo report e nota in `docs/verification/voiceflow-v1-d1.md` sul limite del vecchio controllo smartphone.

Lavoro esterno preservato: `settings.rs`, relativo cambio SQLite in `lib.rs`, sezioni Impostazioni in `docs/desktop.md` e `docs/dictionary.md`. Il registro `docs/execution/voiceflow-improvement-v1.md` appartiene al coordinatore. Documenti congelati, manifest e PDF non modificati. Nessun commit, push, PR o worktree eseguito da questo thread.

## Prove eseguite

- 11 test scheduler passati, comprendenti l'entrypoint di fixture e dieci gruppi comportamentali. I test usano SQLite reale, directory temporanee e processi OS reali. Le modalità di clock/ruolo fittizio sono compilate solo sotto `cfg(test)`.
- Proprietà esclusiva con secondo launcher reale; lease deliberatamente scaduta mentre il worker possiede il lock; figlio del provider `sleep` ancora vivo prima della preemption. Dopo il rilascio il test verifica anche la morte dei PID di provider e discendente.
- Kill del launcher, kill dell'executor, SIGSTOP separato di launcher, executor e watchdog; ripresa del launcher sospeso. Cattura entro il limite senza degrado per tutti i guasti singoli provati. Dopo crash, budget conservato, lotto riprendibile e scratch ripulito solo in assenza del proprietario.
- Crash di un processo che detiene il permit di cattura e dodici gare di avvio fra launcher e cattura, senza acquisire audio. Il blocco rimane attivo durante il permit e sparisce alla morte del processo.
- Timeout della richiesta, revoca del consenso, uscita dalla fascia, annullamento durante il provider e cattura a scheduler SQLite corrotto. Nessun database scheduler richiesto per acquisire il permit di dettatura.
- Fornitore sostitutivo con exit nonzero, errore di turno per rete/auth e output malformato; errori persistenti e nessun retry anticipato. Le fixture simulano i segnali di guasto, non contattano rete o account.
- Clock controllato che guida i processi reali: primavera con 02:30 mancante, autunno con ora ripetuta, stesso istante in fusi diversi, salto all'indietro fuori fascia, scadenza e nuovo avvio fuori fascia dopo il risveglio. Test puri per confini, overnight, durata massima e arretramento del clock nel backoff. Nessuna modifica all'orologio/fuso del Mac.
- Adattatore launchctl simulato: installazione due volte con singolo bootstrap, aggiornamento 03:00–05:00, bootstrap fallito e riparato, bootout fallito con OFF già salvato, disattivazione ripetuta. Plist temporanei; nessuna modifica a `~/Library/LaunchAgents` o ai servizi reali.
- `cargo test --workspace --locked`: 163 passati, 4 ignorati. L'ultima esecuzione completa dopo le riparazioni passa anche con il parallelismo normale del test runner. Include i test delle impostazioni esterne già presenti nel checkout.
- `cargo fmt --all --check` e `cargo clippy --workspace --all-targets --locked -- -D warnings`: passati.
- `cargo build --release --locked -p voiceflow-desktop --bin voiceflow-analysis`: passato. Smoke del vero helper release in directory vuota, due avvii: successo, configurazione OFF persistente e nessun database storico creato. Non attiva launchd o il provider.
- Node 24.18.0 tramite nvm: `pnpm desktop:check`, `pnpm desktop:ui`, `pnpm test:signing` e `pnpm test:desktop-runtime`: passati. Quattro test firma/packaging e tre runtime. La UI build segnala il consueto chunk principale oltre 500 kB, circa 669 kB minificati.
- Browser D2: cinque gruppi passati. Default e sola lettura senza consenso, attivazione separata, fallimento parziale con stato desiderato salvato, riparazione, stato worker/dettatura, disattivazione, helper mancante, esito interrotto, assenza di IPC audio/provider/pubblicazione. Desktop 1060×760 e minimo supportato 780×620 con controllo dei bordi di input e pulsanti. Screenshot aperti e controllati visivamente.
- Regressioni browser D1/B1/C2: 7, 7 e 6 gruppi passati sugli asset compilati. Sono prove React con IPC simulati, non prove del WebView Tauri nativo.
- `git diff --check`: passato. Verifica del manifest congelato eseguita senza modificarlo.

Artefatti ignorati in `artifacts/scheduler-d2`: `result.json`, `settings.png`, `settings-compact.png`, `helper-off.json`, `rust-tests.txt`. Il vecchio screenshot sperimentale a 390 px non è prova valida di supporto mobile. La sidebar della shell desktop ritaglia il pannello a quella larghezza; `document.scrollWidth` da solo dava un falso positivo. D1 contiene una rettifica del relativo claim, preservando prove funzionali e desktop.

## Riparazioni emerse durante le prove

Le prime sonde di concorrenza hanno trovato una race fra lettori dell'intenzione di cattura. I lettori ora usano lock condivisi; solo la cattura prende il lock esclusivo. Il test completo sotto carico ha mostrato che un timeout di un secondo può terminare il processo prima che la shell scriva il primo marker: la prova verifica anche quell'esito, insieme al budget persistito e al rilascio del permit. Non richiede che un processo già scaduto riesca a scrivere.

Un fork concorrente può ereditare brevemente un descrittore CLOEXEC prima dell'exec. La riconciliazione attende quindi il lock per al massimo due secondi. Le osservazioni momentanee di un database occupato non restano latched dopo il recupero; le letture del consenso sono limitate a 500 ms. Il permit Active viene distrutto per ultimo e il fallback mantiene l'intenzione di interrompere l'analisi.

La terminazione forzata non esegue il Drop dello scratch D1. Il worker usa ora un contenitore privato posseduto dal launcher, rimosso dopo il cleanup del gruppo. Se muore il launcher, gli avanzi vengono raccolti al successivo avvio o controllo dello stato, solo dopo acquisizione del lock. Il test multiprocesso verifica anche questa pulizia.

## Limiti dichiarati

L'esclusione è provata nei guasti singoli elencati. Se launcher, executor e watchdog sono tutti sospesi contemporaneamente, o il sistema non li schedula entro il limite, la dettatura continua dopo l'attesa bounded con un avviso e mantenendo l'intenzione. Non viene promessa assenza assoluta di lavoro pesante nel caso simultaneo. Questo limite è stato discusso con il coordinatore; non richiede una nuova architettura per questo lotto.

Un errore di accesso agli stessi file di coordinamento impedisce al worker di iniziare e non rende indisponibile la dettatura. Un errore di launchctl non equivale a un esito provider; l'interfaccia distingue registrazione rilevata da risultato dell'analisi. Nessun costo monetario viene stimato e una richiesta remota già accettata può proseguire dopo la cancellazione locale.

Resta pendente la prova del vero LaunchAgent con consenso nell'app, della CLI remota autorizzata e del flusso nativo durante dettatura reale. La build firmata completa è del coordinatore dopo E; questa verifica compila il helper e controlla il piano di packaging/firma, senza dichiarare già verificato il bundle finale. Nessuna operazione su microfono, playback, sistema audio o dati personali durante questo lotto.
