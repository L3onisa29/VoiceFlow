# VF-06 / D1: verifica implementazione

Data: 25 settembre 2026. Branch `codex/tauri-streaming-app`, baseline applicativa del lotto `60cdfbd3a0372405ed6fd6bc6859e5baf9ae28e9`.

Stato: implementazione pronta per verifica indipendente. Questo report non approva il lotto. Nessun commit, push o PR eseguito dal thread di implementazione.

## Risultato

Lotti locali incrementali e limitati per trascrizioni nuove e correzioni ricordate, anche su vecchie sessioni. I riferimenti durevoli contengono ID e fingerprint, non copie dei testi. Il consenso OpenAI è OFF per default; impostazioni, limiti ed esclusioni per sessione sono persistenti. Il processo Codex è sostituibile, limitato per durata/output e cancellabile, con stdin e output in RAM. La pipeline può aggiungere ipotesi terminologiche e di pulizia attraverso C2; nessun percorso dell'analisi attiva regole.

Il provider lavora fuori dai lock SQLite. Consenso, appartenenza al lotto e validità della fonte sono ricontrollati nella transazione di ogni candidato. Revoca, esclusione, cancellazione della fonte e annullamento della correzione impediscono ingestione non più autorizzata. Candidati parzialmente invalidi hanno conteggi espliciti e checkpoint definiti; output JSON incompleto non importa nulla. Tentativi e riserve di tempo sono persistiti prima dell'avvio, compresi i fallimenti. Il cursore di scansione limita ogni passaggio a 128 eventi e impedisce che fonti troppo lunghe blocchino per sempre le successive.

## File del lotto

- `apps/desktop/src-tauri/src/analysis/`: store, contratto del provider, processo Codex, prompt statico, validatore dello schema e test.
- `apps/desktop/src-tauri/src/history.rs`: una chiamata alla migrazione delle tabelle di analisi.
- `apps/desktop/src-tauri/src/suggestions.rs`: ingestione con guardia di lotto nella transazione C2. Il pubblico `ingest` mantiene il contratto; l'entrypoint interno restituisce la riuscita del commit senza una lettura post-commit che potrebbe fallire dopo l'importazione.
- `apps/desktop/src-tauri/src/lib.rs`: modulo, stato applicativo e quattro IPC per impostazioni/stato/esclusioni.
- `apps/desktop/src/lib/analysis.ts`, `components/analysis/`, `routes/settings/index.tsx`, `components/history/history-editor.tsx`: schema IPC e UI nera/arancione esistente.
- `docs/analysis.md`, `docs/schemas/analysis-output-v1.json`, appendice `docs/suggestions.md`: contratto, policy, schema e API D2.
- `scripts/verify-analysis-ui.ts`: verifica sugli asset compilati con IPC simulati. `scripts/verify-history-ui.ts` aggiunge due risposte fixture per i nuovi IPC privacy.

Lavoro esterno preservato: migrazione SQLite in `settings.rs`, relativo cambio del percorso in `lib.rs`, sezioni Impostazioni in `docs/desktop.md` e `docs/dictionary.md`. Il registro di esecuzione è del coordinatore e non è stato modificato da questo thread. Nel commit D1 il cambio `desktop-settings.json` in `.sqlite3` non appartiene al lotto. Gli allegati congelati e il loro manifest sono invariati e verificati.

## Prove automatiche

- 32 test D1 mirati passati, inclusi i tre test aggiunti nella riparazione sotto. SQLite reale: OFF default, consenso esplicito, riapertura, rollback della migrazione, limiti, checkpoint, crash/reserve, ripetibilità, vecchie sessioni con nuove correzioni, sessioni escluse/eliminate, undo, ID estranei, rifiuti senza nuove evidenze, fonti automatiche separate dalle conferme umane, contesto delle regole ancora corrette, assenza di copie testuali residue e scansione limitata senza starvation.
- Fixture terminologiche, false partenze con eccezioni e ripetizioni percorrono l'ingestione reale. Un test collega il processo CLI sostitutivo alla preparazione e all'ingestione reale di entrambe le categorie. Versione pubblicata sempre zero.
- Processi sostitutivi reali: output strutturato, timeout, cancellazione del gruppo, stdin bloccato, exit nonzero, JSON parziale/malformato, campi extra, eventi strumenti, tetto stdout/stderr e limite di candidati. I test verificano gli argomenti preventivi della CLI.
- Schema locale: campi nullable obbligatori, campi sconosciuti, lunghezze e vocabolario di validazione. La validazione semantica C2 resta separata.
- `cargo test --workspace --locked`: 152 passati, 4 ignorati. Include i test del lavoro esterno alle impostazioni presente nel checkout.
- `cargo fmt --all --check` e `cargo clippy --workspace --all-targets --locked -- -D warnings`: passati.
- Node 24.18.0 tramite nvm: `pnpm desktop:check`, `pnpm desktop:ui`, `pnpm test:signing`: passati, 3 test firma. La UI build conserva il warning informativo del chunk principale, circa 663 kB minificati.
- `git diff --check` e Prettier sui file supportati del lotto: passati.

La verifica browser D1 ha passato 7 gruppi: OFF senza scritture, consenso/limiti espliciti, errore senza falsificare lo stato salvato, contatori/esito parziale, disattivazione, esclusione riaperta nello storico, viste desktop/mobile senza overflow, assenza di IPC provider/pubblicazione/audio. Le verifiche B1 e C2 hanno passato nuovamente tutti i rispettivi gruppi, 7 e 6, sugli stessi asset. Sono prove React con IPC simulati, non prove del WebView Tauri nativo.

Artefatti ignorati: `artifacts/analysis-d1/result.json`, `rust-tests.txt`, `settings.png`, `settings-mobile.png`, `history-excluded.png`, `history-mobile.png`. Gli screenshot desktop e mobile sono stati aperti e controllati visivamente. Le verifiche B1/C2 mantengono i propri report in `artifacts/history-b1` e `artifacts/suggestions-c2`.

## Riparazione dopo la prima verifica indipendente

I due blocchi D1-1 e D1-2 sono stati riprodotti con nuovi test prima delle modifiche: un evento strumento `item.started` seguito da JSON finale valido era accettato; un errore SQLite durante l'INSERT diventava `partial` e consumava la fonte. I test fallivano sul codice precedente.

- D1-1: il parser ora valida il tipo e il testo dell'item anche su `item.started` e `item.updated`. Un test esegue 21 processi sostitutivi per strumenti, tipi sconosciuti e item malformati nelle tre fasi. Un secondo test ammette il ciclo valido di messaggi e reasoning. I flag preventivi sono invariati; il parser resta un controllo successivo e non dimostra isolamento della CLI.
- D1-2: il confine di ingestione marca esplicitamente gli errori semantici con `CandidateRejection` e restituisce `IngestFailure`. Gli altri errori interrompono il lotto come `failed`/`ingest-storage`, senza rifiutare il candidato né avanzare i checkpoint. Il test SQLite introduce un trigger di errore prima del primo candidato e, separatamente, dopo un primo commit. Verifica budget persistente, fonti pendenti, ripresa dello stesso lotto, deduplica di proposta/evidenza, rifiuto non riaperto dopo il retry e versione pubblicata zero.
- Le tre sonde originali in `/tmp/voiceflow-d1-review` sono state ricompilate contro `target/debug/libvoiceflow_desktop.rlib` prodotto da `cargo build --locked -p voiceflow-desktop --lib`. `probe.rs`: `Malformed` per started, updated e completed. `dbprobe.rs`: `failed`, accettati 0, rifiutati 0, pending 1, proposte 0 e lotto riprendibile. `dbprobe-control.rs`: `completed`, accettati 1, rifiutati 0, pending 0 e nessun retry. Output in `probe-repaired.txt`, `dbprobe-repaired.txt`, `dbprobe-control-repaired.txt`; il manifest di build usato per il linking è `repair-build.jsonl`, tutti nella stessa directory temporanea.
- Ripetuti dopo la riparazione: test D1, test workspace, fmt, Clippy e `pnpm desktop:check`. Nessuna modifica a TypeScript, UI o forma degli IPC. `lastError` ammette già codici stringa e il nuovo codice usa il messaggio generico esistente. Le prove UI/build/firma sopra restano quelle della prima implementazione.

La riparazione modifica soltanto `analysis/mod.rs`, `analysis/provider.rs`, i rispettivi test, `suggestions.rs`, `docs/analysis.md` e questo report. Non modifica il registro del coordinatore, i documenti congelati o il lavoro esterno indicato sopra. Non include una chiamata reale a OpenAI né una verifica delle capacità effettive del modello remoto. Il lotto richiede una nuova verifica indipendente; questo report non la sostituisce.

## Contratto D2 e limiti

L'API completa è in [analysis.md](../analysis.md). D2 deve usare `AnalysisStore::open`, `status`, `prepare`, `run` e la trait `Cancellation`; può usare `AtomicBool` o una closure `Send + Sync`. `recover_interrupted` richiede prima la prova che il vecchio worker non possiede più una prenotazione valida. D2 gestisce worker unico, fascia locale, scadenza, coordinamento con dettatura e retry limitati. I budget D1 usano il giorno UTC e sono persistenti anche dopo un crash; i secondi riservati restano distinti dai secondi misurati. L'UI D1 salva soltanto consenso/limiti/esclusioni e non esegue analisi.

La CLI installata `0.157.0` è stata ispezionata in sola lettura con help e lista feature; la documentazione ufficiale è citata in [analysis.md](../analysis.md). Non sono stati letti account o credenziali e non sono stati inviati testi reali o fixture a OpenAI. Nessuna qualità linguistica personale o efficacia del modello remoto viene dedotta dalle fixture.

Le capacità sono disattivate preventivamente dagli argomenti CLI, con ambiente ridotto e directory temporanea. `read-only` da solo non limita le letture a quella directory. Il rifiuto di eventi strumenti è un controllo successivo, non un sostituto dei flag preventivi. Il comportamento effettivo della CLI remota rimane una verifica successiva all'attivazione esplicita nell'app; i test del provider usano eseguibili sostitutivi. Autenticazione/rete non disponibili producono un errore di provider, senza propagare stderr o testo del fornitore nell'archivio.

Nessuna operazione su microfono, playback o sistema audio. Nessuno scheduler/LaunchAgent o job reale creato. Nessuna build firmata finale: resta al coordinatore dopo D/E, come concordato. La verifica nativa/manuale dei flussi precedenti resta separata.
