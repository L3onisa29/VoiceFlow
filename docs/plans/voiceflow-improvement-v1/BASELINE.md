# Baseline congelata | VF-2026-09-25-V1

Data della preparazione: 25 settembre 2026.

## Identità del codice

- Repository: `/Users/davide/Desktop/VoiceFlow`.
- Branch: `codex/tauri-streaming-app`.
- Commit applicativo: `e6877ce0c405d32581a3607c799ed76e53f949aa`.
- Titolo: `feat: consolidate desktop dictionary, history and streaming improvements`.
- Parent: `e890bfd`, precedente versione del buffer e della pulizia locale.
- Il commit applicativo raccoglie 65 file precedentemente modificati o nuovi, inclusi dizionario, storico, navigazione, shadcn, HUD, Bluetooth, coda audio, filtro marcatori e trattamento delle registrazioni brevi.
- Durante la preparazione sono stati corretti soltanto una formattazione Rust nel benchmark e una closure ridondante nel tooltip HUD, necessarie a superare i controlli. Le feature del nuovo piano non sono state implementate in questa attività.

Il repository non risultava inizializzato per GitButler: i commit sono stati creati con Git normale. Il branch non aveva upstream configurato. Nessun pull, push, merge, rebase, cambio branch o worktree. La pulizia consiste nel salvataggio delle modifiche, non nella loro eliminazione.

## Verifiche eseguite sulla baseline

| Verifica                                                                      | Risultato                                     |
| ----------------------------------------------------------------------------- | --------------------------------------------- |
| `cargo test --workspace --locked`                                             | 60 test passati; 4 ignorati                   |
| `cargo fmt --all --check`                                                     | Passato dopo la correzione di formattazione   |
| `cargo clippy --workspace --all-targets --locked -- -D warnings`              | Passato dopo la correzione della closure      |
| `pnpm desktop:check`                                                          | Passato                                       |
| `pnpm desktop:ui`                                                             | Passato                                       |
| `pnpm test:signing`                                                           | 3 test passati                                |
| Prettier sui file modificati supportati                                       | Passato                                       |
| `git diff --check` e controllo staged                                         | Passati                                       |
| `pnpm desktop:build`                                                          | Build macOS release e bundle firmato riusciti |
| `codesign --verify --deep --strict target/release/bundle/macos/VoiceFlow.app` | Passato                                       |

Ambiente: Node 24.18.0 e pnpm 11.5.0. La build finale è stata rieseguita dopo le correzioni di lint. Il bundle generato è `target/release/bundle/macos/VoiceFlow.app`; non è stato avviato in questa attività.

## Limiti che l'esecuzione deve conoscere

- Tre test ignorati richiedono hardware audio o modificano temporaneamente il mute. Il quarto richiede modello e fixture sintetiche per le registrazioni brevi. Non sono stati eseguiti nella preparazione della baseline.
- Nessun nuovo test dal vivo di microfono, playback, focus o incolla. Le prove automatiche non chiudono la segnalazione dell'utente su `<unk>` nell'app utilizzata.
- Vite segnala il chunk principale di circa 607 kB minificati, sopra la soglia informativa di 500 kB. La build riesce; nessun refactor dei bundle è stato aggiunto.
- Il bundle è firmato con l'identità stabile del progetto, ma la notarizzazione non è configurata. Non presentarlo come pacchetto già pronto per distribuzione pubblica.
- Il test per brevi registrazioni documenta limiti della lingua Auto sulle parole isolate molto brevi. Preservare questa evidenza senza estendere il piano a una nuova architettura audio.
- Il dizionario è persistente ma non interviene nella dettatura. Lo storico conserva soltanto il finale. Scheduler, feedback confermato, catalogo estendibile e boosting non sono presenti come feature completate.

## Allegati congelati

- `docs/plans/voiceflow-improvement-v1/PLAN.md`: sorgente del riferimento di cinque pagine.
- `docs/plans/voiceflow-improvement-v1/EXECUTION_PROMPT.md`: mandato dettagliato da inviare all'agente esecutore.
- `docs/plans/voiceflow-improvement-v1/BASELINE.md`: questo documento.
- `output/pdf/voiceflow-improvement-plan-v1.pdf`: allegato statico, cinque pagine A4 controllate visivamente.

Gli SHA-256 dei quattro file sono in `docs/plans/voiceflow-improvement-v1/MANIFEST.sha256`. Verificarli dalla radice del repository:

```sh
shasum -a 256 -c docs/plans/voiceflow-improvement-v1/MANIFEST.sha256
```

Il manifest non include se stesso; la sua versione è fissata dal commit di documentazione che segue il commit applicativo. I checksum rilevano modifiche agli allegati, mentre la cronologia conserva la copia originale. Per ritrovare il commit del pacchetto usare `git log -- docs/plans/voiceflow-improvement-v1`.

Durante lo sviluppo non aggiornare questi allegati. Crea il registro dinamico `docs/execution/voiceflow-improvement-v1.md`; se cambiano i requisiti, conserva la v1 e pubblica una nuova versione esplicita.
