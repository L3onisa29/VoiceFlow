# B1 / VF-02: originali, correzioni e feedback

25 settembre 2026. Implementazione pronta per verifica indipendente. Non costituisce autoapprovazione del lotto.

## Modifiche e prove automatiche

Lo storico SQLite v2 separa originale ASR, risultato prodotto, testo corrente, metadati della sessione, revisioni utente e conferme categorizzate. Le migrazioni e le mutazioni composte sono transazionali. Il risultato originario e le statistiche sono protetti anche da un trigger SQLite. Gli esempi ricordati non attivano regole.

| Controllo                                                        | Esito e copertura                                                                                                                                        |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cargo test -p voiceflow-desktop history::tests --locked`        | 10 passati su SQLite reale temporaneo; migrazione v1, riapertura, rollback, idempotenza, revisioni, conflitto di versione, undo, cancellazione, conteggi |
| `cargo test -p voiceflow-core streaming --locked`                | 15 passati; originali esatti a fine segmento, revisione decoder, pausa ripetuta, silenzio/reset, stop idempotente, fallimento e sessione nuova           |
| `cargo test --workspace --locked`                                | 75 passati, 4 ignorati già previsti. Include i test concorrenti delle impostazioni SQLite                                                                |
| `cargo clippy --workspace --all-targets --locked -- -D warnings` | Passato                                                                                                                                                  |
| `cargo fmt --all --check`                                        | Passato                                                                                                                                                  |
| `pnpm desktop:check`                                             | Passato                                                                                                                                                  |
| `pnpm desktop:ui`                                                | Passato; chunk principale 625,50 kB, warning Vite sopra 500 kB già presente come limite della baseline                                                   |
| `scripts/verify-history-ui.ts`                                   | 7 gruppi passati sulla UI di produzione con IPC Tauri simulato                                                                                           |

La catena sessione, drain, finalizzazione, SQLite e lettura del testo copiabile è esercitata anche nei test `service::tests::stop_drains_backlog_and_delayed_tokens_into_one_history_entry` e `decoder_markers_stay_filtered_in_preview_final_and_new_history_only`. Usano decoder simulati e buffer sintetici in memoria, senza dispositivo audio.

La UI verifica la separazione originale/risultato, conferma assente prima dell'edit, errori di scrittura e retry con la stessa chiave, nessuna conferma automatica, categoria esplicita, copia del testo salvato, undo, assenza del grezzo legacy, dialogo a 780×620, navigazione con registrazione attiva simulata e annullamento dell'eliminazione. Nessun errore browser. Screenshot a 1060×760 e 780×620 ispezionati visivamente.

## Riproduzione della prova UI

Usare Node 24 tramite nvm e il Playwright già installato nell'ambiente. Il runner richiede `PLAYWRIGHT_MODULE`, percorso dell'`index.mjs` di tale installazione. Non installa dipendenze o browser. Gli asset compilati vengono risolti direttamente con `route.fulfill`; non avvia listener o server.

```sh
pnpm desktop:ui
PLAYWRIGHT_MODULE=/percorso/playwright/index.mjs node scripts/verify-history-ui.ts
```

Il runner è versionabile in `scripts/verify-history-ui.ts`. Risultato e screenshot sono in `artifacts/history-b1/result.json`, `correction-1060.png` e `history-780.png`, esclusi dal controllo versione tramite la regola esistente `/artifacts/`. Contengono soltanto fixture, non dettature personali.

## Contratti per i lotti seguenti

- `StreamingSession::original_utterances()` restituisce `None` finché la finalizzazione non riesce, poi una slice di stringhe esatte e ordinate per segmento. Il campo non è presente in `Transcript`, quindi non circola negli eventi frequenti.
- `CompletedDictation` accetta `original_asr: Option<&[String]>` e `processing: Option<&ProcessingMetadata>`. `None` rappresenta una fonte legacy senza dati, non un originale ricostruito.
- `ProcessingMetadata` conserva `model_id`, revisione/provenienza, decoder/processore, formato, cleanup, `groups: Vec<String>`, `dictionary_version: Option<String>` e `cleanup_version`. `Active` lo fissa all'avvio. B2/C devono fornire gruppi e versioni effettivamente usati dalla sessione.
- IPC `history_detail`, `history_edit`, `history_undo`, `history_remember`. Le richieste usano camelCase, negano campi sconosciuti in Rust e validano il dato esterno; Zod verifica i risultati. `history_edit` usa `expectedVersion` e `requestKey`; undo usa `expectedVersion`.
- `history_revisions` conserva `transcription_id`, prima/dopo, parent, stato annullato e chiave idempotente. `confirmed_corrections` riferisce il revision ID e conserva categoria `recognition`, `cleanup` o `style`. Solo queste conferme sono esempi ricordati. Edit libero, risultato prodotto e originale ASR hanno provenienze diverse e non devono diventare implicitamente conferme.
- La cancellazione della sessione elimina le due tabelle derivate B1 tramite FK. Le evidenze future devono riferire questi ID e rispettare la stessa policy, descritta in `docs/history.md`, anche quando vivono in un altro database.

## Limiti e prova manuale pendente

La revisione modello è quella prevista dal manifest incorporato; i file del modello caricati non vengono ricalcolati tramite hash durante la sessione. Il metadato e la UI lo dichiarano. Non prova identità di file modificati manualmente.

La prova UI simula l'IPC. I test Rust esercitano SQLite reale e il collegamento con una sessione a decoder simulato; non verificano il decoder NVIDIA dal vivo, il bridge WebView dell'app installata, il microfono, il focus esterno o l'incolla macOS.

Dopo la build firmata finale, la prova manuale deve completare una dettatura breve con pausa/ripresa, verificare originale e risultato nello storico, salvare due correzioni, ricordarne una per categoria, annullarla, riaprire l'app e controllare statistiche e testi. Infine eliminare la sessione e confermare la rimozione delle revisioni e degli esempi. Non avviata automaticamente. La build release firmata è demandata alla consegna complessiva.

Non è stato creato un corpus personale verificato. Le fixture provano il software, non l'accuratezza ASR o la qualità delle future proposte linguistiche.
