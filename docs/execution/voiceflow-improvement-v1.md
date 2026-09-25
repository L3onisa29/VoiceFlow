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
| A | VF-01 marcatori | Verifica manuale pendente | Controlli indipendenti passati; processo attivo diverso dal bundle |
| B1 | VF-02 storico e feedback | In sviluppo | |
| B2 | VF-03 dizionario operativo | Da iniziare | |
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
