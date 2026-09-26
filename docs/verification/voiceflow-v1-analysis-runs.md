# VF-06/VF-07: storico visibile delle analisi

Data: 26 settembre 2026. Baseline: `f2e58e9`, branch `codex/tauri-streaming-app`.
Stato: implementazione consegnata alla verifica indipendente. Questo documento non costituisce approvazione del lotto.

Verifica indipendente conclusa: **AUTOMATED CHECKS PASSED — MANUAL VERIFICATION PENDING**.
Nessun difetto bloccante. Ripetuti 180 test Rust passati/4 ignorati, fmt/clippy,
typecheck/build UI e sette gruppi della fixture UI, senza errori della pagina.
Probe temporanei collegati alla libreria applicativa compilata hanno verificato
conservazione e paginazione durante inserimenti, eliminazione di revisioni e
rollback/ripetizione della migrazione dopo un errore forzato. I test multiprocesso
di preemption sono passati; la revisione conferma che la diagnostica non modifica
il percorso dei lock di cattura. Installazione e verifica nativa sono rimandate
alla consegna del successivo lotto sulla confidenza richiesto dall'utente.

## Problema riprodotto

Il database conservava tentativi senza esiti propri e conteggi soltanto sul lotto mutabile. L'app esponeva le preferenze e i contatori aggregati, senza elenco/dettaglio dei tentativi. Il test `attempt_history_persists_its_own_outcome` è fallito sulla baseline con il messaggio «attempt outcomes are missing; mutable batch counts cannot describe retries», poi è passato con lo schema v2.

## Consegna

- Migrazione transazionale v1→v2 nello storico reale SQLite; metadati mancanti dei vecchi record nullable, senza ricostruzioni dai conteggi del lotto.
- Pagine deterministiche per coppia lotto/tentativo, dettaglio del singolo esito e lotti preparati senza tentativi. Conservazione integrale dei tentativi.
- Riferimenti alle fonti effettive di ogni tentativo, conteggio originale persistente, rimozione dei riferimenti quando sessioni/revisioni vengono eliminate, stato corrente delle esclusioni.
- Collegamenti tentativo/proposta nella transazione C2: ID, revisione ed effetto creata/aggiornata/esistente; dedup per tentativo. I link aprono la proposta corrente, senza duplicare testi storici.
- Diagnostica degli ultimi 100 avvii che acquisiscono il worker: motivi di mancato invio, esiti e lotto collegato. Scritture diagnostiche best-effort, separate dall'esito operativo e dal percorso della cattura. Interruzioni riconciliate soltanto dopo prova del lock o raccolta del gruppo di processi posseduto.
- Pagina Analisi nella navigazione, stato scheduler distinto da successo dell'analisi, link alle Impostazioni, caricamento/errori/riprova, polling sequenziale e scarto delle risposte di selezioni precedenti.

Non sono state cambiate le regole linguistiche, la selezione immutabile dei gruppi per sessione, la fascia oraria, i budget, il backoff, i lock di cattura o il consenso. La successiva richiesta di confidence e autoapprovazione appartiene a un lotto separato. Nessun punteggio o esito storico viene inventato.

## Verifiche eseguite

| Verifica | Risultato |
| --- | --- |
| `cargo test --workspace --locked` | 180 passati, 4 ignored lasciati tali, zero fallimenti |
| Regressioni analisi mirate | 29 passate |
| Regressioni scheduler mirate | 14 passate |
| `cargo clippy --workspace --all-targets --locked -- -D warnings` | Passato |
| `cargo fmt --all -- --check` | Passato |
| `pnpm desktop:check` | Passato |
| `pnpm desktop:ui` | Passato; warning di dimensione chunk sopra 500 kB |
| `scripts/verify-analysis-runs-ui.ts` sugli asset production | 7 gruppi di verifiche passati, zero pageerror |
| `git diff --check` | Passato |

I test SQLite coprono migrazione e riapertura, fallimento/retry con esiti distinti, completamento senza candidati, risultati parziali, errore di storage dopo commit parziale, dedup e correttezza dei riferimenti, esclusione/eliminazione, cancellazione, crash senza durata inventata, paginazione e input invalidi. I test multiprocesso dello scheduler usano directory temporanee e CLI sostitutive; coprono avvio fuori fascia/disattivato, no-work e fallimento persistente, oltre alle regressioni preesistenti di preemption e proprietà dei processi.

La fixture UI intercetta gli asset di build e l'IPC Tauri in un browser headless, senza listener. Verifica navigazione, consenso OFF senza mutazioni, dettaglio senza suggerimenti, link alla proposta esatta e allo storico esistente, blocco delle fonti escluse, paginazione, errori recuperabili, scarto di risposte obsolete, aggiornamento al focus e completamento attraverso polling. La larghezza minima 780 px non produce overflow orizzontale. Screenshot ispezionato.

Per ripetere la fixture:

```sh
PLAYWRIGHT_MODULE=/percorso/assoluto/playwright/index.mjs node scripts/verify-analysis-runs-ui.ts
```

Gli artefatti fixture sono in `artifacts/analysis-runs/`, esclusa da Git: `ui-checks.json`, `analysis-runs.png`, `analysis-runs-minimum.png`. Contengono soltanto dati inventati di test.

## Limiti e passaggio al coordinatore

Nessun provider reale, microfono, playback, app nativa, LaunchAgent o database personale è stato usato per questa implementazione. La configurazione reale autorizzata dall'utente è gestita separatamente dal coordinatore. La build firmata/installazione e la verifica nativa restano al coordinatore dopo la revisione indipendente e il lotto successivo richiesto dall'utente.

La diagnostica del launcher non registra contendenti che non acquisiscono il lock; conserva al massimo 100 righe. Se l'archivio diagnostico non è scrivibile, il job mantiene il comportamento operativo e può non lasciare una riga diagnostica. Le informazioni vecchie mancanti restano sconosciute. Le proposte collegate sono consultabili nella loro versione corrente, con numero della revisione al tempo dell'ingestione indicato nello storico.

Preservate le modifiche concorrenti preesistenti in `settings.rs`, il cambio JSON→SQLite in `lib.rs`, `docs/desktop.md` e la sezione impostazioni di `docs/dictionary.md`. Il log di esecuzione e il piano follow-up sono di proprietà del coordinatore e non sono stati modificati da questo thread. Nessun commit, push, PR o worktree creato.
