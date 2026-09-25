# VF-01: marcatori e bundle in esecuzione

Verifica del 25 settembre 2026 sul branch `codex/tauri-streaming-app`, HEAD iniziale
`750aa03`, baseline applicativa `e6877ce`. Il manifest dei quattro allegati
congelati passa. Questa nota riguarda il lotto A e non approva il lotto.

## Esito e limite della diagnosi

Il processo attivo usa un eseguibile precedente a quello presente nel bundle.
Il filtro attuale passa le prove automatiche. Non è stato riprodotto un nuovo
risultato contenente `<unk>` nel motore del checkout; non è dimostrato che il solo
riavvio risolva la segnalazione dell'utente. La verifica nell'app resta pendente.

L'indagine ha distinto tre possibilità: processo rimasto sulla build precedente,
ricopia di righe storiche con marcatori, percorso corrente che evita il filtro.
La prima ha una prova diretta, la seconda esiste nei dati archiviati, la terza
non emerge dal tracciamento e dalle fixture. Nessuna modifica al filtro è stata
introdotta per attribuire una causa non riprodotta al decoder.

## Artefatto reale osservato

Comandi in sola lettura:

```sh
ps -p 84236 -o pid,lstart,comm
lsof -p 84236 -a -d txt -Fni
stat -f '%N | inode %i | size %z' target/release/bundle/macos/VoiceFlow.app/Contents/MacOS/voiceflow-desktop
shasum -a 256 target/release/bundle/macos/VoiceFlow.app/Contents/MacOS/voiceflow-desktop
codesign --verify --deep --strict target/release/bundle/macos/VoiceFlow.app
```

- PID `84236`, avviato alle `11:44:22`, eseguibile mappato con inode `200504025`.
- Stesso percorso nel repository, file su disco con inode `200583337`, modificato
  alle `15:10:14`, dimensione `26887680` byte. Orari locali Europe/Rome.
- SHA-256 del binario firmato su disco:
  `2ce00022c9068afbb98a4f38ce080dd4d2ad673bd1754086e9275e8066ee2541`.
- Firma del bundle valida; versione dichiarata `0.2.0`.
- Nessun bundle nelle due posizioni usuali `/Applications/VoiceFlow.app` e
  `~/Applications/VoiceFlow.app`. Questa osservazione non esclude altre copie.

Gli inode diversi provano che il processo non usa il file attualmente presente
allo stesso percorso. L'orario da solo non sarebbe sufficiente. Non è stato
estratto né identificato tramite commit il contenuto dell'immagine mappata.

Una query `sqlite3 -readonly` sullo storico reale ha contato 29 righe, 3 delle
quali contengono `<unk>`: ID 11 alle 11:54:36, ID 18 alle 12:22:28 e ID 24 alle
14:38:03. Le ultime cinque righe non lo contengono; l'ultima è delle 15:17:49.
Questi dati non identificano la versione che ha generato ciascuna riga. Il testo
privato non è stato copiato in questa nota e nessuna riga è stata modificata.

## Percorso del testo

`NemotronDecoder::text` fornisce lo snapshot cumulativo. `StreamingSession::decode`
lo passa a `TextProcessor::update`, che rimuove i marcatori mantenendo gli offset
del testo sorgente. `snapshot` espone `confirmed` e `partial`; la UI li rende
consecutivi senza inserire separatori aggiuntivi.

La pausa e lo stop completano lo stesso processore. `Transcript::text` concatena
le due parti e rimuove soltanto lo spazio esterno. `Worker::stop` assegna quel
valore a `final_text`, lo passa a `platform::paste_text` e lo salva tramite
`HistoryStore::record`. `copy_transcript` usa `final_text`, oppure `Transcript::text`
quando il finale è vuoto. `history_copy` copia il valore archiviato senza
applicare nuove regole. Errori di consegna conservano il testo elaborato.

La policy resta quella della baseline: ogni sequenza completa `<...>` viene
rimossa, inclusi contenuti letterali e sequenze con spazi. Un marcatore incompleto
è nascosto nell'anteprima; un `<` senza chiusura resta nel testo a pausa/stop.
Il filtro mantiene la gestione esistente di punteggiatura, separatori, revisioni
e comandi. Non riscrive le righe storiche già salvate.

## Modifiche circoscritte

- `desktop:verify-runtime` confronta gli inode delle immagini riportate da `lsof`
  con il binario del bundle. Distingue processo corrente, processo precedente,
  eseguibile in altro percorso e app non avviata. La discrepanza termina con
  codice 1 e indica di finire la sessione, uscire dall'app e riaprire il bundle.
- `desktop:build` esegue lo stesso controllo dopo la firma e stampa l'esito.
  Un controllo non disponibile produce un avviso esplicito. La build rimane
  valida anche quando il processo attivo non è ancora stato aggiornato.
- Tre test Node coprono il difetto osservato, inclusa la sostituzione del file
  mantenendo il percorso, processi multipli, altra copia e identità mancante.
- Una fixture Rust aggiunge il percorso sessione, finale e SQLite riaperto in
  tutte le combinazioni di formato e pulizia. Verifica frammentazione, revisione
  del nome, punteggiatura adiacente, pausa/ripresa, stop ripetuto, ripetizioni
  intenzionali, contenuto letterale angolare e marcatore incompleto. Una riga
  storica con `<unk>` rimane identica, la nuova riga contiene il finale filtrato.

La fixture usa il processore e SQLite reali con un decoder fittizio. I valori
numerici passati alla sessione sono dati in memoria: nessun dispositivo audio
viene aperto. Non verifica l'ASR sul parlato, gli eventi Tauri o gli effetti
del clipboard e dell'incolla del sistema; questi ultimi sono stati tracciati
nel codice e restano da provare nell'app.

## Controlli eseguiti

| Comando                                                                                      | Esito                                 |
| -------------------------------------------------------------------------------------------- | ------------------------------------- |
| `cargo test --locked -p voiceflow-core decoder_markers -- --nocapture` prima delle modifiche | 2 passati                             |
| `pnpm test:desktop-runtime`                                                                  | 3 passati                             |
| `pnpm desktop:verify-runtime` sul processo reale                                             | Codice 1 atteso, PID 84236 `stale`    |
| `cargo test --workspace --locked`                                                            | 61 passati, 4 ignorati della baseline |
| `cargo clippy --workspace --all-targets --locked -- -D warnings`                             | Passato                               |
| `cargo fmt --all --check`                                                                    | Passato                               |
| `pnpm test:signing`                                                                          | 3 passati                             |
| Prettier sui file TypeScript/JSON/Markdown toccati                                           | Passato                               |
| `git diff --check`                                                                           | Passato                               |
| Firma del bundle esistente e manifest congelato                                              | Passati                               |

La build completa che esegue il nuovo avviso è demandata all'integrazione finale.
Il comando autonomo ha già eseguito la stessa ispezione sul processo e bundle
reali. Il confronto positivo dopo riavvio richiede l'azione manuale seguente.

## Prova manuale pendente

1. Finire l'eventuale sessione, uscire normalmente da VoiceFlow e riaprire
   `target/release/bundle/macos/VoiceFlow.app`. Eseguire `pnpm desktop:verify-runtime`:
   deve indicare che il processo usa l'eseguibile verificato. Non basta chiudere
   la finestra se il processo resta attivo.
2. Con un nuovo testo che aveva prodotto il problema, osservare anteprima,
   pausa/ripresa e stop. Annotare l'ora e l'ID della nuova riga. Ripetere con
   pulizia disattivata e comandi attivi, verificando i separatori e le parole.
3. Confrontare il finale visibile, Copia testo, incolla nel campo esterno e copia
   della nuova riga dello storico. Nessuna sequenza completa `<...>` deve comparire.
4. Controllare separatamente una vecchia riga con `<unk>`: deve rimanere invariata.
   Se il problema riappare in una nuova sessione, raccogliere versione verificata,
   impostazioni e frammento testuale minimo prima di modificare il motore.

Il processo attivo non è stato interrotto o riavviato dall'agente. Rimane pendente
la conferma del sintomo sul parlato reale e della consegna a clipboard/incolla.
