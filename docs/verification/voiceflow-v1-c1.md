# C1 / VF-04: catalogo dichiarativo di pulizia

25 settembre 2026. Implementazione pronta per verifica indipendente. Non costituisce autoapprovazione. Base applicativa `1ac4fb0`; modifiche esterne alle impostazioni conservate.

## Contratto per C2 e D

`voiceflow_core::cleanup` espone i tipi Serde `Catalog`, `Rule`, `Operation`, `Context`, `Provenance`, `Example` e `CompiledCatalog::compile(catalog, language) -> anyhow::Result<CompiledCatalog>`. I campi JSON sono camelCase, le varianti `operation.kind` kebab-case. Campi sconosciuti e operazioni non supportate sono rifiutati. Gli errori di compilazione sono messaggi leggibili, attualmente in inglese.

`CompiledCatalog::builtin().definition().clone()` restituisce tutti i dati delle 17 regole esistenti, inclusi esempi e provenienza. Il catalogo incorporato è `cleanup-catalog-v1:builtin-1`; il file sorgente è `crates/voiceflow-core/src/cleanup-builtin.json`. Non sono state attivate nuove varianti nell'app.

Le operazioni supportate sono:

- `remove-false-start`, con `source` di 2–7 parole esatte. Elimina soltanto la prima parola e conserva le altre, inclusi grafia e separatori. Nuove varianti di questa operazione sono dati.
- `repeat`, con `word` appartenente alla whitelist italiana preesistente di 14 parole funzionali. Nessuna ripetizione generica.
- `elision`, soltanto `first: "della"` e `prefix: "dell'"` oppure `"dell’"`, seguiti da lettere minuscole. Conserva le due forme preesistenti.

Il catalogo ammette al massimo 256 regole, comprese le incorporate. Ogni regola ha ID univoco, versione positiva, lingua, condizioni ed eccezioni adiacenti, provenienza, riferimenti alle evidenze e 1–16 esempi positivi e negativi. Le parole letterali sono minuscole, massimo 100 byte, con apostrofo ASCII o tipografico. Non ci sono regex, codice, wildcard, catture o testo sostitutivo arbitrario. La cancellazione di cifre, parole capitalizzate e negazioni italiane/inglesi elencate nel compilatore è rifiutata. Il compilatore non interpreta semanticamente nomi minuscoli, numeri scritti in lettere o frasi ambigue: l'autorizzazione e la verifica linguistica restano in C2.

`context` contiene `before`, `after`, `exceptBefore`, `exceptAfter`. Le eccezioni sono al massimo 16 per lato; ogni elemento è una parola letterale. Condizioni contraddittorie, duplicati di ID o operazione/scope e dipendenze oltre sette parole sono rifiutati. L'eventuale contesto destro conta nel limite. Il contesto sinistro usa una parola originale conservata. Punteggiatura interna, newline e pausa impediscono gli abbinamenti che le attraverserebbero. La punteggiatura terminale resta sulla parola sopravvissuta. Spazi multipli e tab tra parole sono ammessi e conservati.

La lingua del catalogo è il codice primario, per esempio `it`; lo snapshot `it-IT` seleziona `it`. `auto` seleziona soltanto `*`. Le regole incorporate hanno lingua `*` perché la baseline applicava già queste forme italiane con ogni selezione del decoder. Questo conserva il comportamento precedente; non è rilevamento automatico della lingua.

Il compilatore verifica gli esempi contro ciascuna regola isolata. Gli esempi hanno massimo 2048 byte per campo, senza spazi iniziali/finali, caratteri di controllo o marcatori. Le prove del lotto eseguono anche gli esempi nel processore reale. Gli esempi non certificano l'effetto con altre regole, dizionario e comandi; C2 deve verificare la configurazione pubblicata nel suo insieme. Le eccezioni sono locali alla regola, non un veto globale ad altre regole.

`TextProcessor::with_rules(mode, cleanup, dictionary, compiled_catalog)` e `StreamingSession::with_rules(decoder, mode, cleanup, dictionary, compiled_catalog)` ricevono lo snapshot per valore. Non esistono mutatori pubblici del catalogo compilato. `definition()`, `version()`, `language()` e `is_empty()` consentono ispezione; `matching_rule()` restituisce l'identità scelta per un input originale. `cleanup_version()` sul processore e sulla sessione restituisce la versione effettiva. Il worker la copia nei metadati B1 dalla sessione istanziata, con `processor_version = voiceflow-text-v3`.

C2 deve assegnare una nuova versione durante la transazione di pubblicazione e compilare/iniettare soltanto regole approvate e attive. `definition()` comprende anche regole di altre lingue; `is_empty()` riguarda la selezione effettiva. Per disattivare tutte le regole si compila un catalogo con `rules: []`; per mantenere soltanto la baseline si usa `builtin()`. Un rollback compila una definizione precedente per la sessione seguente. Non muta lo snapshot di una sessione esistente né riscrive lo storico. Provenienza, versione o successo della compilazione non provano approvazione, autenticità o esistenza delle evidenze. C2/D devono verificarle nello storage. C1 non aggiunge persistenza, pubblicazione, suggerimenti o analisi.

## Ordine e confini

Il filtro marcatori continua a precedere le regole linguistiche. Per ogni posizione originale: comandi protetti, dizionario con relativa gestione dei conflitti, quindi pulizia. Il dizionario può modificare in seguito una parola originale sopravvissuta a una cancellazione; nessuna uscita del dizionario torna nel matcher. Un conflitto del dizionario conserva la frase senza tentare pulizia su quella frase.

Fra regole di pulizia prevalgono dipendenza sorgente più lunga, numero di condizioni richieste, lingua specifica, ID lessicograficamente minore. Tutte le operazioni cancellano una sola parola iniziale: candidati sovrapposti alla stessa posizione non possono produrre sostituzioni discordanti. Il matcher non crea nuove adiacenze da riesaminare. Due cancellazioni adiacenti possono avvenire soltanto se entrambe corrispondono alle rispettive posizioni originali. Ogni token sorgente viene consumato una sola volta.

La baseline senza dizionario conserva il percorso con sette parole rivedibili e una parola congelata. Un catalogo personalizzato usa il percorso B2 con massimo quindici parole trattenute, comprendendo dipendenze congelate e contesto sinistro. La conferma aspetta anche la parola necessaria a escludere un comando sovrapposto. Il limite riguarda parole complete, non byte, transcript accumulato o modello ASR.

## Prove automatiche

| Controllo | Esito |
| --- | --- |
| `cargo test --workspace --locked` | 104 passati, 4 ignorati preesistenti, rieseguito dopo riparazione C1 |
| `cargo test -p voiceflow-core cleanup::tests --locked` | 10 test del catalogo, inclusi esempi eseguiti nel processore reale |
| `cargo clippy --workspace --all-targets --locked -- -D warnings` | Passato |
| `cargo fmt --all --check` | Passato |
| `pnpm desktop:check` | Passato con Node 24 |
| `pnpm desktop:ui` | Passato; warning chunk oltre 500 kB già presente |
| `pnpm test:signing` | 3 test passati |
| Manifest documenti congelati | Quattro checksum invariati |

Le regressioni coprono condizioni, eccezioni, lingua, controlli con negazioni/numeri/nomi/ripetizioni intenzionali/frasi ambigue, modalità fedele, priorità e conflitti del dizionario, output non ricorsivo, revisione della settima parola in 18 posizioni, comandi sovrapposti, frammentazione UTF-8 carattere per carattere e limite di memoria. Una sessione con decoder simulato prova anteprima revisionata, pausa/ripresa, stop ripetuto, originale ASR e versione immutabile. Non è una prova del decoder NVIDIA, microfono o incolla macOS.

## Riparazione C1, tentativo 1

La verifica indipendente ha rilevato due difetti nei confini del matcher. Entrambi sono stati corretti; la riparazione resta da sottoporre a una nuova verifica indipendente.

Il percorso con catalogo personalizzato trasferiva il separatore della parola eliminata sulla sorgente sopravvissuta. Questo alterava le condizioni e le eccezioni della regola successiva. Ora un campo separato conserva il separatore destinato alla resa, sia nella conferma sia nell'anteprima; il matcher continua a leggere i separatori originali. Con A che elimina `alpha` da `alpha beta` e B che elimina `beta` da `beta gamma` eccetto dopo `alpha`, `x\nalpha beta gamma` produce `x\nbeta gamma`. La condizione inversa, B richiesta dopo `alpha`, produce `x\ngamma`. Le regressioni provano spazio, newline, CRLF, tab e spazi multipli, stringhe brevi e output già confermato, frammentazione UTF-8, pausa/ripresa e limiti di 7 parole rivedibili e 15 trattenute.

Il contesto destro poteva attraversare la punteggiatura terminale della sequenza. Ora il matcher considera assente il contesto oltre `, . : ; ! ?`, anche combinati. Una regola che elimina `vorrei` da `vorrei volevo` soltanto prima di `chiedere` conserva `vorrei volevo. chiedere`. Le eccezioni destre rispettano lo stesso confine. Le regole prive di contesto continuano a rimuovere la falsa partenza e conservare la punteggiatura. Test mirati, suite Rust completa, Clippy e formattazione sono stati rieseguiti dopo la correzione. Il riproduttore esterno della verifica indipendente è stato ricompilato contro i sorgenti corretti: entrambi i difetti non si riproducono e i 25.515 confronti della baseline incorporata passano.

## Latenza e qualità

Il benchmark esistente ora include 256 regole di pulizia e la combinazione con 4096 varianti del dizionario. Il corpus sintetico invariato contiene 100.000 parole, 690.000 byte. Le 17 regole incorporate producono corrispondenze reali; una regola verifica la dipendenza di sette parole; gli altri candidati hanno prime parole distinte. Non misura il caso peggiore con 256 regole aventi la stessa prima parola.

Prima misura release su Apple M1 arm64, 8 GiB, macOS 26.6.2, Rust 1.97.1: fedele 22,09 ms; pulizia/comandi 30,61 ms; dizionario 1000 41,43 ms; dizionario 4096 33,87 ms; catalogo 256 32,63 ms; combinazione 33,83 ms. Compilazione della combinazione 1,70 ms, esclusa dal ciclo di aggiornamento. Tutti i casi mantengono sette parole rivedibili; otto totali con baseline, quindici con dipendenze estese. Il carico di compilazione concorrente rende questi tempi informativi, senza dedurre miglioramenti dalla differenza rispetto a B2.

```sh
cargo run --release --locked -p voiceflow-core --bin text-processing-benchmark -- 100000
target/release/text-processing-benchmark 100000 preview
```

Le fixture miste includono italiano, inglese di controllo, termine tecnico, comandi, marcatori e contenuti da preservare. Sono esempi sintetici, non il corpus di 20–30 dettature personali verificate richiesto da VF-08. Qualità personale, latenza dell'intera dettatura e misure audio restano pendenti. La build macOS firmata è demandata alla consegna integrata.
