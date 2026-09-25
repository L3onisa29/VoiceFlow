# VF-08 e VF-09: corpus e prova nativa di boosting

Data: 25 settembre 2026. Lotto sperimentale E, base applicativa `36e88836eb405272d02941a60f053eb63da010bb`. Stato: implementazione e prove dell'autore concluse, pronta per verifica indipendente. Nessuna approvazione implicita del lotto o integrazione del decoder nel prodotto.

## Esito

Il runtime nativo NeMo-Speech.cpp 0.1.0 funziona su questo Mac tramite C ABI, con streaming da file e speech contexts. Il download ufficiale permette la prova senza CMake o installazioni globali. Nel piccolo insieme sintetico valutato, boost 2 non migliora il testo rispetto allo stesso runtime con boost disattivato. Il runtime nativo perde inoltre una frase italiana anche nel CLI ufficiale. La decisione proposta è conservare il decoder ONNX attuale. Non è stato implementato boosting nell'app.

La fattibilità è provata; il beneficio su questi campioni è negativo. Questo risultato non dimostra che ogni peso, modello, voce o backend sia inutile. Nessun corpus personale è stato verificato. Qualità personale e latenza dal microfono all'incolla restano pendenti.

## Artefatti e riproduzione

Il [README dell'esperimento](../../experiments/voiceflow-v1/README.md) contiene tutti i comandi sequenziali, obiettivi di prestazione, definizioni delle metriche e protocollo di raccolta personale senza audio. I sorgenti sono `quality-corpus.rs`, il corpus JSON di 24 sessioni, `native-boost.cpp`, i due script TypeScript, lo script di download e il formato testuale di raccolta. Nessuna modifica al runtime applicativo, alle dipendenze o agli allegati congelati.

Tutti i risultati completi sono sotto `artifacts/boosting`, escluso dal versionamento: `quality.jsonl`, `processor.jsonl`, `preview.jsonl`, `calibration.jsonl`, `selection.json`, `evaluation.jsonl`, `evaluation-initial.jsonl`, `current.jsonl`, `corrected.jsonl`, `summary.json`, `audio-hashes.json`, log e misure `/usr/bin/time -l`. Sono dati sintetici. WAV e modelli non entrano nel commit.

MacBookPro17,1, Apple M1, 8 GiB RAM, macOS 26.6.2 build 25G83. Rust 1.97.1, release ottimizzata; Apple clang 21.0.0; Node 24.18.0, pnpm 11.5.0. Un solo processo con modello per volta. Misure locali singole per decoder, non intervalli statistici. Il carico del Mac può variare.

| Artefatto fissato      | Identità                                                             |
| ---------------------- | -------------------------------------------------------------------- |
| Runtime CPU arm64      | `nemo-speech-0.1.0-macos-aarch64-cpu.tar.gz`, 3.299.661 byte         |
| SHA256 runtime         | `971661d38d4bf97a63c528d13041a964316d25068d8df045e5b4839848092f25`   |
| Modello GGUF           | NVIDIA `nemotron-3.5-asr-streaming-0.6b.q8_0.gguf`, 742.090.464 byte |
| Revisione Hugging Face | `ea30d66debe3740a08b573244286791d423d6b3e`                           |
| SHA256 GGUF            | `3fc991d3badad7277c11030a7519832cddaf2057aafed6d4b25147e953a070b1`   |
| ONNX encoder attuale   | `561818bf1e3d72fbaa99a05fa0c13341b4d72c4503963e74089a70f6d3aeb066`   |
| ONNX decoder attuale   | `7fe1a8c2e247b55bbb8ca917ef64cf60227909c6fe63be2da7ea6fc3858d6a69`   |
| Tokenizer attuale      | `ce3895e40806f02a26c3a225161b96ef682d6c0054bae32a245dec4258d7d291`   |

I due download sono stati verificati contro i digest ufficiali, il secondo anche contro il digest LFS della revisione fissata. Lo script verifica il file prima di estrarlo o usarlo e riusa una copia locale soltanto se il digest coincide.

## VF-08: corpus testuale

24 sessioni sintetiche: quattro di proposta, quattro di calibrazione, sedici di valutazione. Nessun ID appartiene a due insiemi. Due sostituzioni esatte, `tan stack` e `post grass`, derivano soltanto dalle sessioni di proposta dichiarate. Il catalogo di pulizia è quello incorporato. Il corpus include italiano, inglese, termini tecnici, controlli già corretti, negazioni, ripetizioni intenzionali, nomi simili, numeri, comandi, marcatori frammentati, revisione della coda e una sessione lunga di 896 parole. Il confronto usa davvero `TextProcessor`, con snapshot progressivi e finalizzazione.

La verità attesa è stata scritta prima dell'esecuzione e non è stata cambiata per far passare i risultati. Le metriche contano operazioni di modifica allineate a questa verità, non sostituzioni effettuate dal dizionario. La tokenizzazione preserva spaziatura e punteggiatura. Per esempio, un termine composto può richiedere più di un'operazione. Le modifiche applicate all'output senza regole sono utili soltanto quando coincidono con un'operazione attesa nello stesso punto; ogni altra modifica è falsa. Un errore trasformato in un errore diverso conta quindi come modifica falsa, anche se la distanza residua non cambia. L'allineamento deterministico può essere conservativo nei casi ambigui.

| Modalità, 16 sessioni di valutazione | Modifiche utili | Modifiche false | Interventi residui | Sessioni esatte |
| ------------------------------------ | --------------: | --------------: | -----------------: | --------------: |
| Regole disattivate                   |               0 |               0 |                 16 |              11 |
| Terminologia                         |              12 |               0 |                  4 |              14 |
| Pulizia                              |               4 |               0 |                 12 |              12 |
| Combinazione                         |              16 |               0 |                  0 |              16 |

Comandi e filtro marcatori restano comuni alle quattro modalità. Il loro risultato è verificato rispetto alla verità, ma non viene attribuito alle nuove regole. Il risultato zero riguarda soltanto queste fixture: non misura accuratezza personale né copertura di varianti mai viste.

Per ogni caso: una esecuzione di riscaldamento, poi cinque ripetizioni. Il timer comprende aggiornamenti e finalizzazione, escludendo compilazione regole, creazione snapshot e calcolo delle distanze. Mediana della sessione lunga: 239,21 µs senza regole, 298,08 µs terminologia, 141,67 µs pulizia, 270,96 µs combinazione. Queste differenze piccole e variabili non indicano che la pulizia renda più veloce il processore. Massimo osservato fra tutte le ripetizioni heldout: 298,59 µs. Parole sorgente trattenute: massimo 8 senza dizionario e 10 con le due regole di due parole. Il processo del corpus arriva a 37.552.128 byte RSS, incluso il calcolo delle distanze e gli snapshot; non è la memoria del solo processore.

La misura separata esistente da 100.000 parole e 690.000 byte evita la matrice delle distanze:

| Modalità                 | Aggiornamenti, ms | Anteprima a ogni parola, ms |
| ------------------------ | ----------------: | --------------------------: |
| Fedele                   |             36,67 |                       59,12 |
| Pulizia/comandi          |             26,81 |                       75,42 |
| Dizionario 1.000         |             38,88 |                      438,03 |
| Dizionario 4.096         |             33,93 |                      434,44 |
| Catalogo 256             |             32,06 |                      418,79 |
| Combinazione 4.096 + 256 |             33,37 |                      441,93 |

RSS massimo del processo benchmark: 11.288.576 byte senza anteprima e 11.501.568 con anteprima. Compilazione combinata 1,66 ms, separata dal loop. Finestra massima 7 parole rivedibili e 15 trattenute. Gli obiettivi di indagine, fissati dalla baseline B2/C1 prima di qualsiasi ottimizzazione, sono 100 ms per gli aggiornamenti e 1.000 ms con anteprima. Nessun superamento; nessuna ottimizzazione introdotta. Non sono soglie per il decoder e non coprono il caso peggiore di migliaia di regole con la stessa prima parola.

## VF-09: superficie di estensione e protocollo

L'ispezione della dipendenza fissata `parakeet-rs 0.3.8` mostra `NemotronHandle`, selezione della lingua e API di trascrizione, ma nessun parametro pubblico per speech contexts o hook sui logits. In `nemotron.rs`, il loop privato chiama `run_decoder` e `argmax_f32`; `TokenInfo` espone il risultato dopo la scelta. Aggiungere bias richiederebbe una modifica mantenuta della dipendenza e una gestione corretta dei token/blank del transducer. Non è stata fatta.

La [documentazione NVIDIA di personalizzazione](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/v0.1.0/docs/asr/customization.md) dichiara boosting per RNNT cache-aware con tokenizer incorporato e punteggi usuali 2–3. La [configurazione fissata](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/v0.1.0/docs/asr/configuration.md) distingue quei punteggi da quelli CTC. L'[header C](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/v0.1.0/include/nemo_speech/asr.h) offre speech contexts per richiesta e chiamate streaming. Il probe usa direttamente queste chiamate, CPU, right context 1, lingue `it-IT` e `en-US`, PCM16 mono 16 kHz in blocchi di 20 ms, `finish`, drain e rilascio degli handle. Ogni sessione valutata ha esattamente un risultato finale. Un finale vuoto sostituisce un eventuale parziale, non viene nascosto.

Otto file sintetici con Alice/Samantha a 180 parole/minuto, quattro calibrazione e quattro valutazione. Vocabolario: TanStack, PostgreSQL, Convex. Ogni split contiene due controlli con parole simili o nomi. Le voci leggono testi fissati in `boost-corpus.json`; non sono dettature umane. La calibrazione a 0, 2, 3 produce 9 errori normalizzati per ciascun peso. Prima di leggere il heldout, `selection.json` sceglie 2, minimo candidato positivo a parità. Zero resta il controllo e non sarebbe ragionevole abilitarlo nel prodotto sulla base di un pareggio. Nessuna nuova variante è stata ricavata dagli output per migliorare il risultato a posteriori.

Le prime prove di collegamento hanno rilevato l'obbligo di impostare `chunk_size > 0`; il probe è stato corretto prima delle misure. Una prima calibrazione esplorativa usava `it/en`, poi è stata ripetuta con i codici canonici prima della selezione. Con entrambe, i testi di calibrazione comprendevano `We use tan stack and post GRSQL`, `convex per il progetto.`, `The ten stackable boxes on the grass`, `non chiama Mario`. Boost 2/3 cambiava soltanto la maiuscola di Convex. La valutazione è stata ripetuta dopo aver aggiunto il controllo esplicito del numero di finali e la gestione del finale vuoto; la selezione è rimasta congelata e tutti i testi sono identici a `evaluation-initial.jsonl`.

## Risultati decoder

La metrica audio usa parole Unicode in minuscolo, ignorando punteggiatura e maiuscole. Non equipara numeri scritti in lettere e cifre. I 27 token di riferimento appartengono a quattro sessioni heldout. Le modifiche utili/false sono allineate alla verità rispetto al controllo senza boost dello stesso runtime.

| Percorso                          | Modifiche utili | Modifiche false | Errori residui | Sessioni esatte normalizzate |
| --------------------------------- | --------------: | --------------: | -------------: | ---------------------------: |
| Attuale ONNX                      |               0 |               0 |              5 |                          2/4 |
| Attuale + correzioni              |               0 |               0 |              5 |                          2/4 |
| Nativo GGUF, boost 0              |               0 |               0 |              9 |                          1/4 |
| Nativo GGUF, boost 0 + correzioni |               2 |               0 |              7 |                          2/4 |
| Nativo GGUF, boost 2              |               0 |               0 |              9 |                          1/4 |
| Nativo GGUF, boost 2 + correzioni |               2 |               0 |              7 |                          2/4 |

Le correzioni passano attraverso lo stesso `TextProcessor`. Sull'ONNX le varianti nuove `tand stack` e `post GSQL` non corrispondono alle due regole fissate. Non aggiungerle dopo avere letto il heldout è essenziale per mantenere il confronto valido. Nel nativo, la regola già fissata corregge `tan stack`; questo beneficio esiste anche senza boosting.

Casi che limitano il risultato:

- `be02`, riferimento `Collego PostgreSQL alla scheda numero quattro.`: nativo restituisce stringa vuota a boost 0 e 2, con un finale regolare. ONNX produce `Collego post GSQL alla scheda numero 4`.
- `be03`, `The complex convex shape is not a stack.`: entrambi i runtime conservano tutte le parole; nessuna introduzione del vocabolario favorito.
- `be04`, nomi Mario/Marina e negazione: preservati. Il nativo rende `ventidue` come `22`, contato come un errore di resa nel confronto esatto; non come perdita del valore numerico.
- In calibrazione il nativo omette `Configuro` e `Marco`, anche senza boost. Il percorso attuale applica preroll e VAD propri. Non si attribuiscono queste differenze al boosting.

Il risultato vuoto è riprodotto separatamente dal CLI ufficiale con blocchi di 160 ms, stessa lingua, stesso right context, stesso file WAV e nessun boost. Exit code 0 e testo vuoto:

```sh
artifacts/boosting/nemo-speech/bin/nemo-speech transcribe artifacts/boosting/audio/be02.wav --model artifacts/boosting/model.gguf --backend cpu --language it-IT --stream --verbatim --no-warmup --asr.streaming.rnnt_right_context 1
```

Questo esclude una spiegazione limitata al parser PCM o ai blocchi da 20 ms del probe. Non determina la causa interna del runtime. Nessuna riparazione sperimentale dell'audio o del decoder è stata introdotta.

## Tempi, memoria e distribuzione

Ultimo run nativo: creazione recognizer 40,63 ms, poi warmup distinto 1.100,11 ms. I pesi sono mappati, quindi la sola creazione non rappresenta un caricamento a freddo completo. Heldout senza boost 435–653 ms per file; boost 2 475–725 ms. Primo parziale 103–191 ms senza boost e 103–151 ms con boost, escluso il caso vuoto che non ha parziali. Finalizzazione 50–117 ms. RSS massimo del processo 965.771.264 byte, circa 921 MiB.

Attuale ONNX, processo separato per file: caricamento heldout 1,177–1,179 s, elaborazione da file 721–886 ms, inferenza dichiarata dal benchmark 719–885 ms, primo parziale 220–386 ms, finalizzazione 280–285 ms. RSS massimo heldout 1.200.816.128 byte, circa 1.145 MiB. Il nativo conserva un modello caldo; l'ONNX riparte per file e applica preroll/VAD del prodotto. Questi numeri non dimostrano un vantaggio prestazionale fra decoder. Sono misure di calcolo da file, senza tempo reale, focus, incolla o microfono. L'effetto isolato del boost non è distinguibile dal rumore temporale di questo campione.

Il pacchetto CPU estratto occupa circa 9,2 MiB e il nuovo modello 707,7 MiB. Il SDK usa dylib con `@rpath` e backend ggml/BLAS; `otool -L` conferma dipendenze relative e librerie di sistema. La [documentazione SDK](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/v0.1.0/docs/sdk.md) richiede di preservare la disposizione dei file runtime. Una distribuzione nell'app richiederebbe binding Rust con ownership/cancellazione, packaging e firma delle dylib, download/modello e rollback, porting del preroll e prove streaming complete. Metal è disponibile fra le release ufficiali ma non è stato misurato. Nessuna stima monetaria o promessa di costo zero: la prova non usa un servizio remoto di inferenza, ma richiede storage, CPU e lavoro di integrazione.

Il runtime dichiara [Apache-2.0](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/v0.1.0/LICENSE); l'archivio include LICENSE, NOTICE e licenze dei componenti. Le [third-party notices](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/v0.1.0/THIRD_PARTY_NOTICES.md) includono anche componenti opzionali con termini diversi, fra cui KenLM LGPL. Non si può ridurre la verifica distributiva dell'intero pacchetto alla sola etichetta Apache. La [model card fissata](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/blob/ea30d66debe3740a08b573244286791d423d6b3e/README.md) dichiara [OpenMDW-1.1](https://openmdw.ai/license/1-1/), che richiede conservazione di licenza e avvisi applicabili nella distribuzione. Questa è un'ispezione degli artefatti, non una garanzia legale o un'approvazione alla distribuzione pubblica.

## Verifiche e limiti residui

- `cargo test --workspace --locked`: 165 passati, 4 ignorati preesistenti. I due nuovi test coprono scoring, errore sostituito da altro errore, inserimenti ripetuti e separazione/provenienza delle fixture.
- `cargo clippy --workspace --all-targets --locked -- -D warnings`: passato dopo correzione di un loop nel solo runner.
- `cargo fmt --all --check`: passato.
- Compilazione release dei tre benchmark e compilazione C++ con `-Wall -Wextra -Werror`: passate; inferenza reale da file eseguita.
- `node experiments/voiceflow-v1/measure.ts test`: passato; scoring eseguito sugli output reali, inclusi output vuoti e controlli negativi.
- Manifest congelato: quattro checksum invariati. UI invariata, verifiche D2 riutilizzabili; build firmata finale demandata al coordinatore.

La raccolta personale ha un formato testuale e un protocollo ripetibile, ma non contiene dati personali verificati né audio. L'esperimento non prova stabilità su lunghe sessioni audio, energia, prestazioni Metal, lingue automatiche, notarizzazione o integrazione con l'interfaccia. La verifica indipendente deve ancora confermare codice, riproducibilità e conclusioni di questo lotto.
