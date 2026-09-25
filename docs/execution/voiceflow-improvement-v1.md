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
| B1 | VF-02 storico e feedback | Verifica manuale pendente | Verifica indipendente senza difetti bloccanti |
| B2 | VF-03 dizionario operativo | In sviluppo | |
| C1 | VF-04 catalogo | Da iniziare | |
| C2 | VF-05 suggerimenti | Da iniziare | |
| D1 | VF-06 analisi | Da iniziare | |
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

Riaperti i riferimenti NVIDIA [customization](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/asr/customization.md) e [configuration](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/asr/configuration.md): RNNT e CTC hanno meccanismi/pesi diversi, da verificare sul runtime prima di misurare un vantaggio. Questa ricognizione non completa l'esperimento E.

### B1 / VF-02

[Report e contratti B1](../verification/voiceflow-v1-b1.md). Originale ASR per segmento finalizzato, risultato prodotto, revisioni e conferme separate; metadati fissati all'avvio. Migrazione SQLite v2 transazionale, trigger di immutabilità, protezione delle modifiche concorrenti e statistiche originarie preservate. UI per modifica/annullamento/conferma categorizzata. Nessuna attivazione di regole.

Implementatore: 10 test SQLite, 15 streaming, 75 test workspace passati e 4 ignorati; clippy/fmt, typecheck e UI build passati. Sette gruppi di prova UI con IPC simulato e asset di produzione, senza server. Verifica indipendente: `AUTOMATED CHECKS PASSED — MANUAL VERIFICATION PENDING`, nessun blocco. Ripetuta la matrice e aggiunte prove avverse su dettaglio ritardato, errore di caricamento, risposta persa dopo commit e conflitto di revisione: passate. Screenshot ispezionati. Prova reale nel bundle firmato ancora pendente. B2 può usare i contratti verificati senza dipendere dall'audio reale.
