# Esecuzione VoiceFlow improvement v1

Riferimento: VF-2026-09-25-V1. Avvio: 25 settembre 2026.

## Baseline verificata

- Repository `/Users/davide/Desktop/VoiceFlow`, branch `codex/tauri-streaming-app`.
- HEAD iniziale `750aa03`, baseline applicativa `e6877ce0c405d32581a3607c799ed76e53f949aa`. Working tree iniziale pulito.
- I quattro checksum del manifest passano. Prompt, piano, baseline e PDF di cinque pagine letti; restano immutabili.
- GitButler non inizializzato (`but status --json`: `setup_required`); operazioni con Git normale sul branch corrente.
- Node 24.18.0 tramite nvm, pnpm 11.5.0, Rust/Cargo 1.97.1.
- Piano già approvato. Implementazione, verifica indipendente in sola lettura ed eventuali riparazioni in contesti distinti. Nessun worktree, push o PR.
- Nessun server, microfono, playback o modifica al sistema audio autorizzati. Le prove reali necessarie restano pendenti; si prosegue sulle attività indipendenti.

## Stato dei lotti

| Lotto | Requisiti | Stato | Evidenze / commit |
| --- | --- | --- | --- |
| A | VF-01 marcatori | Verifica manuale pendente | `cf987b9`; controlli indipendenti passati |
| B1 | VF-02 storico e feedback | Verifica manuale pendente | `39ef4ef`; verifica indipendente senza difetti bloccanti |
| B2 | VF-03 dizionario operativo | Verifica manuale pendente | `1ac4fb0`; verifica indipendente e 5.360 casi aggiuntivi |
| C1 | VF-04 catalogo | Verifica manuale pendente | `d501551`; seconda verifica passata dopo riparazione |
| C2 | VF-05 suggerimenti | Verifica manuale pendente | `60cdfbd`; seconda verifica passata |
| D1 | VF-06 analisi | Verifica manuale pendente | Seconda verifica passata; commit in preparazione |
| D2 | VF-07 scheduler | Da iniziare | |
| E | VF-09 boosting | Da iniziare | |
| Trasversale | VF-08 qualità | Da iniziare | Fixture distinte da parlato personale |

## Verifiche e problemi

Le evidenze di ciascun lotto sono registrate sotto, distinguendo implementazione, controlli automatici e prove nell'app. Un test con fixture non chiude la segnalazione reale su `<unk>`.

### Baseline automatica ripetuta

`pnpm desktop:check`, `pnpm desktop:ui` e `pnpm test:signing` passano. Resta l'avviso noto sul chunk di circa 607 kB.

### Lavoro concorrente preservato

Durante A sono apparse modifiche esterne a `settings.rs`, `lib.rs`, `docs/desktop.md` e `docs/dictionary.md`: migrazione delle impostazioni JSON a SQLite. Non appartengono al lotto A e non saranno incluse nel suo commit. I lotti seguenti devono rispettarle e integrare gli eventuali punti condivisi senza ripristinare la baseline.

### A / VF-01

Implementazione e prove: [report A](../verification/voiceflow-v1-a.md). L'immagine eseguibile mappata dal PID 84236 ha inode 200504025; il bundle firmato sullo stesso percorso ha inode 200583337. Questo prova la differenza di versione, non la risoluzione del sintomo con il riavvio. Aggiunti controllo `desktop:verify-runtime`, avviso dopo build e regressioni del percorso sessione/storico. Nessuna modifica al filtro e nessuna riscrittura delle righe storiche.

Implementatore: 61 test Rust passati, 4 ignorati preesistenti; clippy/fmt, 3 test runtime Node e 3 signing passati. Verifica indipendente: `AUTOMATED CHECKS PASSED — MANUAL VERIFICATION PENDING`, senza blocchi. Ripetuti 27 test core, 3 marcatori, 3 runtime, 3 firma, typecheck, fmt, Prettier, firma e manifest; discrepanza del processo confermata direttamente. Riavvio manuale, nuova dettatura, copia/incolla effettivo restano pendenti. La build completa sarà eseguita sul risultato integrato. B1 è indipendente dalla riproduzione sul parlato e può proseguire.

### VF-08 / baseline del processore prima di B2

Apple M1 arm64, 8 GiB, macOS 26.6.2, Rust 1.97.1, build release. `cargo run --release --locked -p voiceflow-core --bin text-processing-benchmark -- 100000`: 100.000 parole, 690.000 byte; fedele 16,99 ms, pulizia/comandi 18,34 ms; massimo 7 parole modificabili e 8 trattenute. Ripetizione con `/usr/bin/time -l target/release/text-processing-benchmark 100000`: 17,72 e 16,19 ms; RSS massimo 7.979.008 byte, footprint massimo 3.212.416 byte. Solo processore testuale sintetico, esclusi ASR, audio, IPC e UI. Il modulo di formattazione è ancora invariato rispetto alla baseline; le modifiche B1 al grezzo non sono esercitate da questo benchmark.

Obiettivo prima delle nuove regole: mantenere la finestra limitata e il lavoro per aggiornamento indipendente dalla lunghezza già consolidata. Confrontare regole disattivate/attive sullo stesso corpus e misurare l'incremento; nessuna percentuale di accuratezza sul parlato deducibile da questa prova. Le misure dell'intera dettatura e il corpus personale verificato restano pendenti.

### Ricognizione dei contratti esterni per D/E

CLI locale `codex-cli 0.157.0`, risolta da PATH tramite nvm. `codex exec --help` conferma `--output-schema`, `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, `--sandbox read-only`, stdin e `--output-last-message`. `codex features list` espone `shell_tool`, `unified_exec`, `multi_agent` e `skill_search`: il lotto D deve limitare esplicitamente strumenti e configurazione, non affidarsi solo al prompt. Nessuna richiesta di analisi effettuata. Verificata la [documentazione ufficiale non interattiva](https://learn.chatgpt.com/docs/non-interactive-mode).

La [configuration reference ufficiale](https://learn.chatgpt.com/docs/config-file/config-reference) conferma i controlli `features.shell_tool`, `features.unified_exec`, `web_search` e persistenza della cronologia. La CLI installata espone inoltre `apps`, `plugins`, `hooks`, `in_app_browser` e `skill_search`, da considerare nell'isolamento dell'analizzatore. Non è stato letto né modificato il contenuto della configurazione o delle credenziali dell'utente.

Riaperti i riferimenti NVIDIA [customization](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/asr/customization.md) e [configuration](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/asr/configuration.md): RNNT e CTC hanno meccanismi/pesi diversi, da verificare sul runtime prima di misurare un vantaggio. Questa ricognizione non completa l'esperimento E.

### B1 / VF-02

[Report e contratti B1](../verification/voiceflow-v1-b1.md). Originale ASR per segmento finalizzato, risultato prodotto, revisioni e conferme separate; metadati fissati all'avvio. Migrazione SQLite v2 transazionale, trigger di immutabilità, protezione delle modifiche concorrenti e statistiche originarie preservate. UI per modifica/annullamento/conferma categorizzata. Nessuna attivazione di regole.

Implementatore: 10 test SQLite, 15 streaming, 75 test workspace passati e 4 ignorati; clippy/fmt, typecheck e UI build passati. Sette gruppi di prova UI con IPC simulato e asset di produzione, senza server. Verifica indipendente: `AUTOMATED CHECKS PASSED — MANUAL VERIFICATION PENDING`, nessun blocco. Ripetuta la matrice e aggiunte prove avverse su dettaglio ritardato, errore di caricamento, risposta persa dopo commit e conflitto di revisione: passate. Screenshot ispezionati. Prova reale nel bundle firmato ancora pendente. B2 può usare i contratti verificati senza dipendere dall'audio reale.

### B2 / VF-03

Implementazione in verifica indipendente, [report B2](../verification/voiceflow-v1-b2.md). Varianti esplicite, lingua, condizioni/eccezioni di parole adiacenti, gruppi manuali e snapshot immutabile. Matching sul sorgente senza catene di sostituzioni. La finestra revisionabile rimane di 7 parole; le dipendenze delle espressioni più lunghe richiedono contesto sorgente congelato, fino a 15 parole complete complessive, sempre limitato. Nessuna query per parola. Dizionario non leggibile: dettatura senza regole e avviso visibile.

Prime prove dell'implementatore: UI B2 6 gruppi e regressione UI B1 7 gruppi passati. Misura sintetica finale del solo aggiornamento, 100.000 parole: fedele 14,52 ms, pulizia 14,95 ms, 1.000 varianti 31,81 ms, 4.096 varianti 32,97 ms. Con richiesta di anteprima a ogni parola: 59,70 / 63,76 / 451,33 / 457,54 ms. RSS circa 10,75 MB. Da confermare nella revisione; non sono misure di ASR né del parlato personale.

Verifica indipendente: `AUTOMATED CHECKS PASSED — MANUAL VERIFICATION PENDING`, nessun blocco. 93 test Rust, 4 ignorati preesistenti, fmt/clippy, typecheck/UI build, firma 3 test e UI B2/B1 ripetuti. Ulteriori 5.360 casi eseguibili contro il sorgente reale passati, con frammentazione, revisioni consentite, venti offset di confine, frasi lunghe, comandi, condizioni, conflitti e testi di controllo. Benchmark ripetuto: 4.096 regole 33,10 ms aggiornamenti e 457,31 ms con anteprima; limiti 7/15 rispettati. Rimangono prova ASR/WebView/incolla reale e build firmata finale. C1 può procedere sui contratti automatici verificati.

### C1 / VF-04

Catalogo dichiarativo implementato, in preparazione per verifica indipendente. Regole builtin estratte in dati, schema con operazioni finite, lingua, condizioni/eccezioni, provenienza, versione ed esempi positivi/negativi. Nessun codice o regex arbitrario. Iniezione di catalogo immutabile nel processore e nella sessione, con versione effettiva riportata nei metadati; pubblicazione e UI saranno C2.

Implementatore: 102 test Rust passati, 4 ignorati preesistenti, clippy/fmt, typecheck/UI build e 3 test firma. Esempi eseguiti sul processore, revisione di frasi lunghe, interazioni con dizionario/comandi, pausa/ripresa e grezzo ASR verificati. Benchmark esteso a 256 regole di pulizia combinate con 4.096 varianti; limiti 7 parole modificabili e 15 trattenute rispettati. Nessuna prova reale con audio.

Prima verifica indipendente: difetto riprodotto nelle condizioni sul sorgente dopo una cancellazione. Il processore trasferisce un separatore al token sopravvissuto e lo usa anche per decidere la regola successiva, confondendo presentazione e sorgente originale. Il caso con newline modifica l'applicazione di `before`/`exceptBefore`. C2 attende riparazione e nuova verifica in un contesto distinto.

Il revisore ha inoltre riprodotto una condizione destra che attraversava un punto fermo. Esito `NOT APPROVED`. Un nuovo contesto di riparazione ha separato `rendering_separator` dai separatori sorgente immutabili in anteprima e consolidamento, e bloccato il contesto destro attraverso la punteggiatura terminale. Due regressioni aggiunte: 10 test catalogo, 104 workspace passati e 4 ignorati, clippy/fmt passati. Riproduttore del revisore ricompilato: entrambi i difetti risolti e 25.515 confronti builtin conservati. Nuova verifica indipendente in corso, nessun avanzamento a C2 prima dell'esito.

Seconda verifica indipendente: `AUTOMATED CHECKS PASSED — MANUAL VERIFICATION PENDING`. Entrambi i difetti verificati risolti sul sorgente attuale, 25.515 confronti baseline passati, prove aggiuntive di catalogo vuoto, segmenti vuoti/marcatori, reset e pulizia disabilitata passate. Workspace 104/4, clippy/fmt, diff e manifest passati. Nessun blocco al lotto C2; prova reale e build firmata integrate restano pendenti.

### C2 / VF-05

Implementati revisione e pubblicazione atomica delle proposte, archivio delle versioni e snapshot combinati, con UI shadcn. [Report C2](../verification/voiceflow-v1-c2.md). Proposte ed evidenze restano separate dalle regole approvate; cancellazione delle sorgenti coordinata e conservazione delle definizioni approvate spiegata nell’app. Contratto di ingestione per D1 documentato con schema.

Implementatore: 116 test Rust passati e 4 ignorati preesistenti, fmt/clippy, typecheck/UI build, 3 test firma; UI C2 6 gruppi, B2 6 e B1 7 passati. Il revisore indipendente ha ripetuto i controlli e riprodotto un difetto: un rifiuto basato su una revisione ricordata si riapre aggiungendo il risultato elaborato della stessa sessione. Nessuna nuova sessione o conferma. Il lotto attende l’esito finale della revisione e una riparazione in un contesto distinto.

Prima verifica C2: `NOT APPROVED`. Due blocchi riprodotti via API pubblica e SQLite temporaneo: riapertura dalla vista elaborata della medesima sessione e accettazione di `voice flow` dentro `voice flow\u{0301}` come evidenza su parole complete. Verificatore: 116/4 Rust, fmt/clippy, typecheck/build UI, firma 3, UI C2/B2/B1 6/6/7 e manifest passati. Avviata riparazione circoscritta con regressioni; D1 attende nuova verifica indipendente.

Ricognizione E: la release NVIDIA `v0.1.0` pubblica un archivio CPU macOS arm64 da 3.299.661 byte, SHA-256 `971661d38d4bf97a63c528d13041a964316d25068d8df045e5b4839848092f25`, e una variante Metal. Verificato il catalogo ufficiale GitHub Releases via API in sola lettura; non è necessario installare CMake per tentare il runtime nativo. Nessun runtime aggiuntivo eseguito in questa ricognizione.

Riparazione C2: modificati solo il modulo suggerimenti e il report. Quattro regressioni aggiunte, tre fallivano prima della correzione; 16 test mirati e 120 workspace passati, 4 ignorati, fmt/clippy passati. Il probe indipendente originale, ricompilato, mantiene il rifiuto e respinge il falso confine Unicode. Avviato un nuovo contesto di verifica in sola lettura.

Seconda verifica C2: `AUTOMATED CHECKS PASSED — MANUAL VERIFICATION PENDING`. Probe originali ricompilati e passati; ulteriori 72 casi di confine, 6 casi di output umano e 6 permutazioni delle evidenze passati. Workspace 120/4, fmt/clippy, diff e manifest passati. Evidenze UI/TypeScript/firma della prima verifica riutilizzate perché invariate. Nessun blocco riproducibile; prove ASR/WebView/focus/incolla reali e build firmata finale ancora pendenti. D1 può procedere sui contratti verificati.

Ricognizione D2: manuale `launchd.plist` della macchina e [guida Apple](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html) confermano che `StartCalendarInterval` recupera al risveglio le scadenze durante lo sleep, mentre `StartInterval` può perderle. Il worker deve comunque riverificare autonomamente la fascia locale: il risveglio non autorizza lavoro dopo le 06:00. Nessun LaunchAgent installato o avviato durante questa ricognizione.

### D1 / VF-06

Implementazione in corso. Contratto previsto: archivio analisi dedicato con preparazione, esecuzione tramite provider sostituibile, stato, configurazione, esclusioni e recupero. Checkpoint per eventi, includendo nuove correzioni ricordate di sessioni precedenti. I cambi di consenso/esclusione invalidano i lotti preparati; l’ingestione verifica nella stessa transazione appartenenza alle fonti autorizzate e autorizzazione corrente. Il contesto delle proposte esclude quelle collegate a sessioni escluse e omette motivazioni/esempi testuali. Nessuna analisi remota di dati reali avviata.

Ricognizione E aggiuntiva: [header C pubblico della release v0.1.0](https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/v0.1.0/include/nemo_speech/asr.h) espone `nemo_speech_asr_speech_context` con frasi/peso nelle opzioni di riconoscimento, anche streaming. Un harness nativo può quindi tentare boosting senza server. La [scheda del modello NVIDIA](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b) dichiara licenza OpenMDW 1.1, distinta da Apache 2.0 del runtime. Da verificare sugli artefatti esatti durante E.

D1, prime prove dell’implementatore: 24 test mirati passati su migrazioni SQLite, consenso, riapertura, rollback, entrambe le categorie linguistiche, correzioni di vecchie sessioni, checkpoint/retry, fonti escluse/eliminate/annullate/estranee, assenza di pubblicazione, input ostile e guasti di processo. Risolti lotti vuoti persistenti e ambiguità da rilettura fallita dopo ingestione già confermata. Typecheck/build UI passati; matrice completa e prova UI in corso. Nessuna approvazione del lotto ancora emessa.

D1 pronto per verifica indipendente: [report](../verification/voiceflow-v1-d1.md), [API e policy](../analysis.md). 29 test D1, workspace 149 passati e 4 ignorati, fmt/clippy, typecheck/build UI, firma 3. UI D1/B1/C2 7/7/6 gruppi passati. Budget prenotato prima della spawn e mantenuto dopo crash; cursore limitato evita blocco da fonti troppo grandi. La CLI reale rimane da provare dopo consenso nell’app: i processi di test sono sostitutivi. Avviato nuovo contesto di revisione in sola lettura.

Prima verifica D1: `NOT APPROVED`. Matrice 149/4, fmt/clippy, TypeScript/build/firma, UI 7/7/6 e manifest passati. Due probe indipendenti riproducono: eventi strumenti `item.started`/`item.updated` non respinti; errore SQLite nell’ingestione classificato come candidato invalido con avanzamento definitivo del checkpoint. Un nuovo contesto ripara solo questi blocchi, includendo retry dopo guasto a metà lotto. D2 attende nuova verifica.

Riparazione D1: validazione degli elementi CLI in ogni fase e distinzione esplicita fra rifiuto del candidato e errore infrastrutturale. Entrambe le regressioni fallivano prima del fix. Test mirati 32, workspace 152 passati/4 ignorati, fmt/clippy/typecheck/diff/manifest passati. Probe originali ricompilati: eventi strumenti respinti; guasto SQLite produce errore con fonte ancora pendente e retry disponibile. Regressione di guasto a metà lotto verifica deduplica senza riaprire rifiuti. Avviata seconda verifica indipendente.

Seconda verifica D1: `AUTOMATED CHECKS PASSED — MANUAL VERIFICATION PENDING`. Originali ricompilati, 48 ulteriori varianti di eventi e cinque prove avverse SQLite passate: rollback evidenze, guasto a metà lotto/riapertura/deduplica/rifiuto, JSON corrotto, finalizzazione fallita, candidato semanticamente invalido. Workspace 152/4, fmt/clippy/typecheck/diff/manifest passati. UI/build/firma invariati riutilizzati. Nessun blocco; restano CLI reale dopo consenso e WebView nativo. D2 può procedere.
