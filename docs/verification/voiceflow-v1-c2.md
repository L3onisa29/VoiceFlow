# C2 / VF-05: revisione e pubblicazione dei suggerimenti

25 settembre 2026. Implementazione pronta per verifica indipendente, senza autoapprovazione. Base `d501551`. Modifiche esterne alle impostazioni SQLite conservate. Commit e registro di esecuzione restano al coordinatore.

## Comportamento implementato

Pagina shadcn nella navigazione esistente. Terminologia e pulizia hanno definizioni strutturate, prima/dopo, lingua, gruppi, condizioni/eccezioni, motivazione, esempi e controlli, fonti reali e conflitti. Stati proposto, rimandato, rifiutato, approvato/attivo e approvato/disattivato. Approva/pubblica, modifica, rifiuta con motivo, rimanda, disattiva, riattiva e ripristina una pubblicazione precedente. Salvataggio e ingestione non attivano regole.

SQLite conserva proposte, ID delle fonti, audit senza copie dei payload e definizioni versionate. Deduplica per comportamento/lingua/gruppi normalizzati. Un rifiuto riemerge solo con una nuova sessione o una diversa revisione ricordata; il risultato elaborato della stessa sessione non è nuova conferma. Contatori derivati dagli ID effettivi, senza frequenze dichiarate dal modello. Una conferma annullata non resta etichettata come umana e non autorizza una nuova approvazione.

La pubblicazione valida le regole combinate nel processore reale fuori dai lock di scrittura e rilegge versioni ed evidenze durante la transazione finale. I conflitti con il dizionario manuale bloccano anche approvazione e ripristino. Trigger e chiavi esterne coordinano cancellazione dello storico e dati derivati. La policy visibile all'approvazione e alla cancellazione distingue le definizioni approvate indipendenti dalle fonti rimosse.

Il worker acquisisce insieme dizionario e catalogo approvato prima della cattura. Archivi del dizionario, pubblicazioni e catalogo incorporato permettono di ricostruire le configurazioni conservate; le revisioni B2 anteriori all'aggiornamento non vengono inventate. Pausa/ripresa usa lo stesso snapshot e il ripristino non modifica lo storico.

## Prove automatiche

| Controllo | Esito |
| --- | --- |
| `cargo test -p voiceflow-desktop suggestions::tests --locked` | 16 passati su SQLite reale temporaneo dopo repair 1 |
| `cargo test --workspace --locked` | 120 passati, 4 ignorati preesistenti dopo repair 1 |
| `cargo clippy --workspace --all-targets --locked -- -D warnings` | Passato |
| `cargo fmt --all --check` | Passato |
| `pnpm desktop:check` con Node 24 | Passato |
| `pnpm desktop:ui` | Passato; chunk 655,91 kB, warning oltre 500 kB già presente nella baseline |
| `pnpm test:signing` | 3 passati |
| `scripts/verify-suggestions-ui.ts` | 6 gruppi passati, IPC Tauri simulato e asset di produzione |
| `scripts/verify-dictionary-ui.ts` | 6 gruppi B2 passati |
| `scripts/verify-history-ui.ts` | 7 gruppi B1 passati, rieseguiti dopo aggiornamento della policy di eliminazione |
| Manifest congelato | Quattro checksum invariati |
| Probe esterno C2 ricompilato dopo repair 1 | Stessa sessione ancora `rejected`; falso match con accento combinante rifiutato |

I dodici test C2 iniziali coprono ciclo completo, riapertura, versioni precedenti, snapshot immutabile, deduplica/rifiuto/nuove evidenze, purge di proposte e audit, conservazione esplicita delle definizioni approvate, conflitto con regole manuali, interazione terminologia/pulizia, retry idempotente, revisioni obsolete, fallimento transazionale iniettato, riferimenti falsi, conferme assenti/annullate, due istanze concorrenti, ricostruzione dopo modifiche manuali, edit senza attivazione, rollback di schema incompleto e sessione streaming con decoder simulato attraverso pausa/ripresa e cambi di gruppo/pubblicazione.

### Repair 1 dei rilievi C2-1 e C2-2

C2-1: dopo un rifiuto fondato soltanto su una revisione umana, originale ASR e risultato elaborato della medesima sessione aggiungevano una vista e riaprivano erroneamente la proposta. I riferimenti automatici ora cercano qualsiasi evidenza già collegata alla sessione. Una revisione umana diversa resta nuova evidenza soltanto dopo la verifica che sia ricordata e non annullata. Inserimento dei riferimenti, revisioni della proposta e pubblicazione conservano le transazioni esistenti.

C2-2: i confini dell'esempio ora seguono i token separati da whitespace del processore e consentono come suffisso soltanto la punteggiatura terminale supportata `,.:;!?`. Accenti combinanti, apostrofi e sintassi di URL/codice appartengono al token. La stessa verifica vale per l'input sorgente e l'output della correzione umana. Nessuna normalizzazione Unicode viene inventata.

Quattro regressioni aggiunte coprono revisione umana seguita da entrambe le viste automatiche in entrambi gli ordini, fonte automatica seguita da una prima e da una diversa revisione umana, revisioni annullate, retry prima e dopo il rifiuto, nuova sessione, accento combinante singolo e combinazioni di più segni, input e output umani, esempi Unicode completi e punteggiatura legittima. Prima della correzione, tre di queste regressioni fallivano e la suite C2 riportava 13 passati e 3 falliti. Dopo la correzione, i 16 test C2, la suite completa, Clippy e `cargo fmt --all --check` passano. La UI non cambia; i risultati UI e manifest nella tabella appartengono alla verifica iniziale e non sono stati rieseguiti per questa riparazione.

Il sorgente esterno `/tmp/voiceflow-c2-review-probe/main.rs` è rimasto invariato ed è stato ricompilato in `/tmp/voiceflow-c2-review-probe/probe-repair` contro la libreria aggiornata. Il probe originale riproduceva `rejected` seguito da `proposed` e accettava il falso match. Quello ricompilato restituisce:

```text
before same-session produced evidence: rejected
after same-session produced evidence: rejected; sessions: 1; refs: 2
combining-mark suffix falsely accepted as whole-word evidence: false
```

La riparazione attende una nuova verifica indipendente. Le prove manuali descritte sotto restano pendenti.

Il test di migrazione aggiuntivo ha rilevato che SQLite può accettare un trigger su una tabella preesistente incompleta senza validarne subito le colonne. La migrazione ora prepara letture dei campi richiesti prima di confermare. La regressione verifica rollback dello schema originario e successiva riapertura corretta.

La UI esercita categorie/provenienza/ID reali delle fixture, conteggi, blocco conflitti, nessuna scrittura alla lettura, dialogo di approvazione e annullamento, errore e retry con la stessa chiave, disattivazione e ripristino, editor pulizia a 780×620, aggiunta/rimozione esempi con minimo uno, salvataggio senza attivazione, rinvio, motivo obbligatorio di rifiuto, sorgenti eliminate e navigazione con registrazione simulata. Nessun comando di registrazione inatteso e nessun errore browser. Screenshot a 1060×760 e 780×620 ispezionati; il contenitore di scorrimento dell'editor è stato corretto dopo ispezione visiva.

```sh
PLAYWRIGHT_MODULE=/percorso/playwright/index.mjs node scripts/verify-suggestions-ui.ts
```

Il runner legge gli asset compilati e li restituisce tramite `route.fulfill`. Usa un Playwright già disponibile, senza installare browser. Prove in `artifacts/suggestions-c2/result.json`, `rust-tests.log`, `review-1060.png`, `edit-780.png`, `active-780.png`; contengono solo fixture. I runner B1/B2 non sono stati modificati.

## Contratto per D1

Documentazione completa in [suggestions.md](../suggestions.md), schema in [suggestion-ingest-v1.json](../schemas/suggestion-ingest-v1.json).

`voiceflow_desktop::suggestions` espone `SuggestionStore::open(history_path, dictionary_path)`, `ingest(IngestRequest)`, `snapshot()` e `archived_session_snapshot(dictionary_revision, publication, language)`. `IngestRequest` contiene `candidate` e 1–64 `EvidenceRef`. Un riferimento usa `sessionId` e uno fra `revisionId` ricordato/non annullato o `originalIndex` zero-based. Entrambi null indicano il risultato elaborato, mai una conferma umana. Le fonti devono contenere un esempio positivo su confini di parola; per revisioni umane, anche l'output deve essere presente nel testo corretto.

`Candidate` contiene `definition`, `language`, `groupIds`, `reason`, `positiveExamples`, `negativeExamples`. `definition.category` distingue terminologia e pulizia. Le operazioni di pulizia sono quelle C1. La validazione Rust nega campi sconosciuti, conta i riferimenti dal database e non accetta dal modello stati, versioni o indicatori di conferma umana. `ReviewSnapshot` espone lo stato reale, i conflitti, le pubblicazioni e gli ID effettivi dopo ripristino.

D1 deve chiamare `ingest`, non `review` per attivare regole. Ogni ingestione è atomica; un batch D1 di più proposte dovrà gestire esplicitamente gli esiti parziali. Nuovi esempi non possono alterare silenziosamente una definizione già revisionata. L'API non interpreta riscritture come sostituzioni. Nessun analizzatore, scheduler o invio remoto è implementato da C2.

## Limiti e verifiche manuali pendenti

Le fixture dimostrano motore, SQLite, snapshot streaming con decoder simulato e interfaccia compilata. Non dimostrano qualità personale delle proposte, decoder NVIDIA reale, WebView installata, microfono, focus esterno o incolla macOS. Nessun corpus personale è stato inventato.

La validazione combinata copre esempi dichiarati, controlli fissi e scopes documentati; non dimostra assenza di modifiche indesiderate su ogni frase possibile o su ogni sottoinsieme di gruppi. Nessuna misura di latenza end-to-end audio è attribuita a questo lotto.

La build macOS firmata finale è demandata al coordinatore dopo l'integrazione D/E. Dopo quella consegna, verificare nell'app una proposta da una fonte autorizzata, approvarla, cambiare pubblicazione durante dettatura, mettere in pausa/riprendere e confermare applicazione solo nella sessione successiva. Disattivare e ripristinare, riaprire l'app, eliminare la sorgente e controllare la rimozione dei riferimenti e la conservazione della definizione approvata secondo il consenso mostrato. Questa prova resta pendente.
