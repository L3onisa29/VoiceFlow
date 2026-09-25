# B2 / VF-03: dizionario operativo

25 settembre 2026. Implementazione pronta per verifica indipendente. Non costituisce autoapprovazione del lotto. Base di lavoro `39ef4ef`, con modifiche esterne alle impostazioni conservate.

## Comportamento e contratti

SQLite v2 conserva varianti confermate, grafia preferita, lingua, gruppi, corrispondenza esatta o minuscolo Unicode, condizioni adiacenti ed eccezioni. La selezione dei gruppi vive nel database del dizionario. Migrazione, mutazioni e incremento della versione sono transazionali. La migrazione rilegge la versione dopo il lock per gestire aperture concorrenti. Le vecchie parole migrano senza varianti e non attivano sostituzioni.

`DictionaryStore::session_snapshot(language)` restituisce un dizionario compilato, i gruppi selezionati come `id:nome` e `dictionary-v2:<revision>`. La lingua delle impostazioni viene ridotta al codice primario: `it-IT` usa `it`, `en-US` usa `en`. `auto` applica soltanto regole con lingua `*`. Il worker fissa questi dati prima della cattura; pausa/ripresa conserva la stessa istanza. Modifiche successive non mutano la sessione in corso. `processing_snapshot` collega la configurazione effettiva ai metadati B1 e, in caso di errore del dizionario, restituisce regole vuote, nessuna versione e un avviso visibile.

Il modulo puro `voiceflow_core::dictionary` espone `Rule`, `Context`, `MatchMode`, `Dictionary::compile`, `Dictionary::apply` e `Match`. `Context` è dichiarativo e limitato a parole adiacenti. `apply` riceve parole e separatori originali, contesto sinistro e token protetti dai comandi; restituisce consumo sorgente e sostituzione oppure conflitto. Le operazioni C possono usare gli stessi confini e lo stesso contratto di consumo senza reinterpretare il testo già prodotto. B2 non implementa il catalogo di pulizia C e non attiva esempi B1 o proposte del modello.

`TextProcessor::with_dictionary` e `StreamingSession::with_dictionary` accettano la configurazione compilata per valore. La struttura non offre mutatori pubblici. Il processore mantiene sette parole rivedibili e ritarda il consolidamento delle dipendenze di una frase. Con il dizionario attivo conserva al massimo quindici parole complete contando coda, parole congelate e contesto sinistro; il massimo verificato dopo un aggiornamento è `DICTIONARY_RETAINED_WORDS = 15`. La modalità senza varianti usa ancora il percorso precedente, con massimo otto parole trattenute. Un token incompleto resta escluso dal conteggio, come nella baseline.

Le regole consumano fino a sette parole, incluso l'eventuale contesto destro. Prima del consolidamento viene congelata anche la parola necessaria a escludere un comando sovrapposto. Non viene rielaborato l'intero transcript. Comandi, sostituzioni e pulizia operano sull'originale della finestra; gli output delle sostituzioni non rientrano nel motore. Le parentesi angolari nelle grafie operative sono rifiutate per non reintrodurre i marcatori filtrati nel lotto A.

La precedenza è frase più lunga, maggior numero di condizioni richieste, lingua specifica, gruppo specifico, modalità esatta. Le eccezioni eliminano candidati prima del confronto. Un conflitto di pari rango mantiene l'intera frase originale, senza applicare una variante più corta. La pagina mostra conflitti potenziali compatibili per lingua, contesto e punteggiatura terminale anche tra gruppi selezionabili insieme.

L'IPC aggiunge `dictionary_select_groups(ids)`. Snapshot e richieste usano camelCase; la UI valida le risposte con Zod, Rust rifiuta campi sconosciuti. Nessuna query per parola, accesso remoto o modifica del decoder. Uso completo in [docs/dictionary.md](../dictionary.md).

## Prove automatiche

| Controllo                                                        | Esito                                                                                                                                                                       |
| ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cargo test -p voiceflow-core dictionary::tests --locked`        | 10 test su parole intere, Unicode, apostrofi, punteggiatura, URL/codice, negazioni/numeri, no cascata, specificità, conflitti, comandi, condizioni e limite di sette parole |
| `cargo test -p voiceflow-desktop dictionary::tests --locked`     | 14 test su SQLite reale, migrazione v1, riapertura, rollback schema/dati/versione, aperture concorrenti, selezione/lingua, immutabilità, conflitti e cancellazione          |
| `cargo test --workspace --locked`                                | 93 passati, 4 ignorati preesistenti; include sessione reale con decoder simulato, pausa/ripresa, originale ASR e metadati/fallback del servizio                             |
| `cargo clippy --workspace --all-targets --locked -- -D warnings` | Passato                                                                                                                                                                     |
| `cargo fmt --all --check`                                        | Passato                                                                                                                                                                     |
| `pnpm desktop:check`                                             | Passato                                                                                                                                                                     |
| `pnpm desktop:ui`                                                | Passato; chunk principale 630,87 kB, warning Vite oltre 500 kB già presente                                                                                                 |
| `pnpm test:signing`                                              | 3 passati                                                                                                                                                                   |
| `scripts/verify-dictionary-ui.ts`                                | 6 gruppi passati su UI di produzione con IPC simulato                                                                                                                       |
| `scripts/verify-history-ui.ts`                                   | 7 gruppi B1 passati; fixture del dizionario aggiornata al protocollo v2                                                                                                     |

La regressione delle sette parole prova 18 diversi offset di confine e modifica l'ultima parola mentre la prima è già fuori dalla coda rivedibile. Nessuna parte della sostituzione viene consolidata prematuramente. Un'altra prova alimenta il processore carattere per carattere, controlla il limite di memoria e confronta il risultato batch. La sessione con decoder simulato conserva le sostituzioni durante pausa/ripresa e l'originale ASR separato.

La UI verifica selezione gruppi durante registrazione simulata, modifica e conferma varianti, lingua, normalizzazione, condizioni, eccezioni, rifiuto visibile di otto parole, errore di scrittura e retry, conflitti, termine senza varianti e navigazione senza comandi inattesi. Screenshot a 1060×760 e 780×620 ispezionati. Nessun errore browser. Il runner usa `route.fulfill` sugli asset compilati senza listener.

```sh
PLAYWRIGHT_MODULE=/percorso/playwright/index.mjs node scripts/verify-dictionary-ui.ts
```

Output locali esclusi dal controllo versione: `artifacts/dictionary-b2/result.json`, screenshot e `rust-tests.log`. Il percorso Playwright usato è quello già installato nell'ambiente di Codex; il runner non installa dipendenze o browser.

## Latenza e memoria

Stessa macchina della baseline, Apple M1 arm64, 8 GiB, macOS 26.6.2, Rust 1.97.1, release. Corpus sintetico invariato di 100.000 parole e 690.000 byte. Il benchmark include una variante che corrisponde al corpus, una frase di sette parole e varianti con primi token distinti. Non rappresenta un corpus personale né il caso peggiore di migliaia di varianti con lo stesso primo token.

| Configurazione                    | Baseline prima B2 | B2 aggiornamento | B2 aggiornamento e anteprima a ogni parola |
| --------------------------------- | ----------------: | ---------------: | -----------------------------------------: |
| Fedele, nessuna regola            |    16,99–17,72 ms |         14,52 ms |                                   59,70 ms |
| Pulizia e comandi, nessuna regola |    16,19–18,34 ms |         14,95 ms |                                   63,76 ms |
| Pulizia, comandi, 1000 varianti   |      non misurata |         31,81 ms |                                  451,33 ms |
| Pulizia, comandi, 4096 varianti   |      non misurata |         32,97 ms |                                  457,54 ms |

La ripetizione B2 immediatamente dopo compilazione ha misurato 22,15 / 26,12 / 40,17 / 33,91 ms. I tempi risentono di riscaldamento e carico del Mac; le differenze sul percorso senza regole non provano un miglioramento. Nel run con `/usr/bin/time -l`, 4096 varianti richiedono 0,330 µs per parola per l'aggiornamento, circa 0,180 µs in più della pulizia senza dizionario nello stesso run. Anteprima a ogni parola con 4096 varianti richiede 4,575 µs per parola. La compilazione dei 4096 candidati richiede circa 1,43 ms ed è separata dal ciclo di aggiornamento.

RSS massimo del processo con i quattro casi 10.747.904 byte; footprint massimo 5.932.160 byte. Baseline senza dizionario 7.979.008 e 3.212.416 byte. Memoria del processo complessiva, non attribuzione isolata al solo matcher. Sette parole modificabili in tutti i casi, otto trattenute senza regole, quindici con la dipendenza più lunga. Non è una misura del decoder, dell'audio, del tempo dall'ultima sillaba al testo o dell'IPC.

```sh
cargo run --release --locked -p voiceflow-core --bin text-processing-benchmark -- 100000
/usr/bin/time -l target/release/text-processing-benchmark 100000
target/release/text-processing-benchmark 100000 preview
```

## Limiti e prova manuale pendente

La build macOS firmata è demandata alla consegna integrata. Le fixture provano motore, SQLite e UI, non il riconoscimento NVIDIA reale, il bridge WebView installato, il microfono o l'incolla macOS. Nessun corpus personale verificato è stato inventato.

Dopo la consegna firmata, creare un gruppo e una variante breve, selezionare il gruppo e dettare un esempio. Cambiare variante durante la sessione, mettere in pausa e riprendere: il risultato deve mantenere lo snapshot iniziale. Nella sessione seguente deve usare il nuovo. Ripetere con gruppo disattivato, frase di sette parole, conflitto esplicito e comando parlato; verificare originale, risultato e gruppi/versione nello storico dopo riapertura. Il test manuale resta pendente.

La revisione numerica identifica la configurazione usata, ma B2 non archivia copie di ogni configurazione precedente. Il rollback/pubblicazione del catalogo è compito del lotto C. Condizioni e lingua appartengono al termine e si applicano a tutte le sue varianti; non è prevista selezione automatica del contesto. La normalizzazione non include NFC, accenti, apostrofi equivalenti o fuzzy matching.
