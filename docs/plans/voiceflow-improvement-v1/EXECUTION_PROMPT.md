# Prompt di esecuzione | VoiceFlow improvement v1.0

ID immutabile: VF-2026-09-25-V1. Data: 25 settembre 2026.

## Mandato

Implementa il piano approvato di miglioramento di VoiceFlow descritto in questo prompt e nel PDF allegato `voiceflow-improvement-plan-v1.pdf`. Porta a termine i lotti A-D e la valutazione sperimentale E; non fermarti dopo il primo miglioramento. Esegui un lotto alla volta, mantenendo un registro di avanzamento, verifiche e commit. Non richiedere nuovamente l'approvazione delle attività già comprese nel piano; chiedi soltanto decisioni nuove che cambiano sostanzialmente il perimetro o autorizzazioni realmente mancanti.

Questa è una richiesta di implementazione quando il prompt viene inviato all'agente esecutore. La preparazione degli allegati non ha già implementato le feature. Le approvazioni dei suggerimenti linguistici dentro l'app sono una funzione del prodotto, distinta dall'approvazione del piano di sviluppo.

## Riferimento statico e precedenza

- Repository previsto: `/Users/davide/Desktop/VoiceFlow`.
- Branch previsto: `codex/tauri-streaming-app`.
- Leggi `docs/plans/voiceflow-improvement-v1/BASELINE.md`: contiene commit applicativo esatto, controlli eseguiti e limiti noti.
- Leggi `docs/plans/voiceflow-improvement-v1/PLAN.md` e `output/pdf/voiceflow-improvement-plan-v1.pdf`. Il primo è la sorgente testuale dello stesso piano di cinque pagine.
- Verifica il manifest `docs/plans/voiceflow-improvement-v1/MANIFEST.sha256` dalla radice del repository. Non modificare i quattro documenti congelati o il manifest per adattarli a posteriori all'implementazione.
- Usa `docs/execution/voiceflow-improvement-v1.md` per stato, decisioni, prove e commit. Se serve un nuovo requisito, proponi un documento v2 o un addendum esplicitamente approvato; la v1 resta leggibile e invariata.
- Le nuove istruzioni esplicite dell'utente e gli AGENTS applicabili prevalgono sul documento. Se prompt e PDF sembrano in conflitto, segnala il punto; non scegliere silenziosamente la versione più comoda.

## Avvio e baseline

1. Leggi le istruzioni del repository e la skill human-gated-development applicabile. Ispeziona branch, HEAD, stato, documentazione e codice prima di modificare. Non presumere che il checkout sia ancora identico alla baseline allegata.
2. Conserva tutte le modifiche successive o concorrenti dell'utente. Individua quali parti del piano risultano già realizzate e verificane l'effetto prima di duplicarle. Non eseguire reset, pulizie distruttive o riscritture della storia.
3. Rileva GitButler: usalo se inizializzato e funzionante; altrimenti usa Git normale. Resta sul branch corrente. Non creare worktree di alcun tipo. Non eseguire push, merge, rebase o creare PR senza richiesta specifica.
4. Usa Node 24 tramite nvm e pnpm come già nel progetto; Rust/Tauri per il backend locale e React/TypeScript/shadcn per l'interfaccia. Non introdurre un backend ospitato, Python o un database a grafo per queste feature.
5. Compila il registro con baseline, lotti e criteri. Stati: da iniziare, in sviluppo, in verifica, da riparare, verifica manuale pendente, completato, bloccato con causa concreta.

## Confini e comportamenti da preservare

- Streaming locale, modello caricato nel processo, buffer di sette parole e relativo contesto di confine, comandi italiani e inglesi, preferenza di pulizia opzionale.
- Stop/pausa, coda audio, finalizzazione, cancellazione, focus e incolla, protezione Bluetooth e firma stabile sono baseline da preservare. Non riprogettarli per opportunità.
- Mai rimuovere negazioni, numeri, nomi o ripetizioni intenzionali attraverso euristiche generiche. "Molto molto importante" e "quel ragionamento di prima non va bene" restano fedeli. "La dispositivo" non richiede correzione grammaticale nella v1.
- Nessuna conservazione audio, training personale, modello Jev, riscrittura generale o selezione automatica del contesto. Nessuna acquisizione delle correzioni fatte in altre app.
- Non avviare server, listener localhost, watcher o preview persistenti. Usa controlli statici, build e server già esistenti se presenti. Non avviare microfono, riproduzione, test hardware o modifiche dell'audio senza richiesta esplicita. Se un criterio richiede tale verifica, mantienilo pendente e descrivi una prova breve e ripetibile.
- Nessun diagramma come sorgente Mermaid, ASCII o catena di frecce. Se serve un diagramma, deve essere una grafica realmente renderizzata e leggibile.

## Lotti da implementare

### A / VF-01: filtro dei marcatori, prima verifica

Il codice contiene già il filtro di `<unk>` e delle sequenze complete `<...>`, con test verdi. L'utente segnala che non funziona nell'uso reale: non dichiararlo risolto solo leggendo il codice.

Riproduci il sintomo con il percorso reale più vicino disponibile, controlla quale bundle è effettivamente utilizzato e segui i dati in anteprima, finale, copia/incolla e storico. Distingui vecchie righe archiviate dai nuovi risultati. Non modificare retroattivamente lo storico per nascondere il problema.

Conserva la policy già richiesta per tutte le sequenze complete `<...>`; documenta che include i contenuti letterali fra parentesi angolari. Gestisci marcatori frammentati, parole adiacenti, punteggiatura, revisioni del decoder, pausa e stop. Non modificare la policy dei marcatori incompleti senza motivazione esplicita. Aggiungi una regressione che intercetti il difetto effettivo, se riprodotto. Se il problema è il bundle non aggiornato, correggi la consegna e verifica il bundle, senza inventare modifiche al motore.

Accettazione: nessun marcatore completo nei nuovi risultati e nessuna perdita o concatenazione errata. Verifica manuale eventualmente pendente deve rimanere dichiarata. Lo sviluppo degli altri lotti può procedere soltanto se indipendente da quel riscontro.

### B1 / VF-02: originali, correzioni e feedback

Estendi lo storico delle nuove sessioni per conservare separatamente originale ASR, risultato elaborato e revisioni confermate dall'utente, insieme a lingua, gruppi e versioni di modello/regole necessarie a riprodurre il trattamento. Non salvare audio né ogni snapshot intermedio.

Le vecchie righe devono mantenere i dati e rappresentare esplicitamente l'assenza del grezzo. Fornisci modifica del testo, annullamento della correzione e azione "Ricorda questa correzione". Distingui errore di riconoscimento, errore di pulizia e preferenza stilistica. Una riscrittura libera non genera automaticamente sostituzioni.

Migrazioni transazionali, idempotenza del salvataggio e compatibilità con dati precedenti sono obbligatorie. Non alterare conteggi o durata della dettatura originale quando si edita il testo. Definisci la rimozione dei dati derivati quando una sessione viene cancellata: niente copie testuali nascoste nelle evidenze. I dati già inviati a un fornitore esterno non devono essere dichiarati cancellati senza una capacità effettiva.

Accettazione: prove su SQLite reale, riapertura, rollback, assenza di originale, correzioni multiple, cancellazione e statistiche coerenti.

### B2 / VF-03: dizionario operativo

Estendi termini e varianti con grafia preferita, lingua, gruppi, condizioni ed eccezioni. Selezione manuale dei gruppi. Carica uno snapshot immutabile in RAM all'inizio della dettatura; gli aggiornamenti diventano disponibili alla sessione seguente.

Applica sostituzioni esatte o esplicitamente normalizzate su parole complete ed espressioni confermate. Non introdurre fuzzy matching fonetico generalizzato. Un termine privo di varianti non autorizza a inventarle. La regola più specifica prevale; un conflitto senza soluzione conserva l'originale ed è visibile nella gestione delle regole.

Integra le sostituzioni nel processore esistente senza rielaborazione ricorsiva del risultato. Limita le espressioni alla capacità esplicita del buffer: quelle troppo lunghe vanno segnalate come non applicabili, non spezzate o troncate. Proteggi comandi, apostrofi, Unicode, punteggiatura e revisione della coda. Nessuna query per parola e nessuna chiamata al modello remoto durante la dettatura.

Accettazione: stessi input e versione danno lo stesso risultato; prova di attivazione/disattivazione dei gruppi, frasi di controllo e latenza confrontata con baseline.

### C1 / VF-04: catalogo estendibile di pulizia

Estrai un insieme limitato di operazioni dichiarative per varianti di false partenze e ripetizioni. Mantieni separate le regole linguistiche dalle sostituzioni terminologiche e dai comandi. Conserva i comportamenti esistenti come casi di regressione.

Ogni regola conserva versione, condizioni, eccezioni, lingua, esempi positivi e negativi e provenienza. Il motore applica regole all'originale della finestra in un ordine definito; evita cicli e applicazioni duplicate. Rifiuta regole non supportate, ambigue o con costo non limitato. Non eseguire codice o espressioni arbitrarie prodotti dal modello.

Accettazione: nuove varianti aggiungibili come dati per le operazioni supportate, senza regressioni sui confini, senza interpretazione semantica estesa e senza modificare involontariamente la modalità fedele.

### C2 / VF-05: revisione e pubblicazione dei suggerimenti

Crea una pagina shadcn coerente con la navigazione esistente. Mostra categoria, prima/dopo, contesto, motivazione, evidenze reali, conflitti e stato. Azioni: approva, modifica, rifiuta, rimanda, disattiva e ripristina.

Separa ipotesi del modello, correzione confermata e regola attiva. Deduplica proposte uguali; non ripresentare un rifiuto senza nuove evidenze identificabili. Approvazione e pubblicazione costituiscono un'operazione atomica/versionata. Nessun aggiornamento in corsa della dettatura. Il rollback non riscrive trascrizioni passate.

Accettazione: autorizzazione esplicita nell'app per le nuove regole, versioni tracciabili, conflitti segnalati e ripristino testato. L'attivazione automatica delle proposte resta fuori dalla v1.

### D1 / VF-06: analisi notturna di termini e pulizia

Prepara localmente lotti di nuove trascrizioni, correzioni confermate e proposte ancora rilevanti. Integra Codex CLI come analizzatore sostituibile con output strutturato. Verifica la documentazione e il contratto della CLI installata prima dell'integrazione; nessun percorso assoluto legato a una sua versione.

Il processo deve trovare nuove parole, varianti errate, famiglie circoscritte di false partenze, condizioni ed eccezioni, e regole esistenti che l'utente continua a correggere. Non limitarti a estrarre termini: l'ampliamento della pulizia è parte obbligatoria del lotto.

Validazione dello schema e delle operazioni proposte; riferimenti, frequenze e conteggi confermati dal codice sui dati reali. Tratta il testo dettato come dati non attendibili, anche se contiene istruzioni. Limita l'analizzatore ai dati preparati: niente modifiche dirette al repository, ai database attivi o alla configurazione; non abilitare modalità prive di controlli per comodità.

Non considerare una sostituzione automatica preesistente come conferma umana: impedisci il rinforzo circolare degli errori. Proposte ambigue restano ipotesi. Errori, timeout, output parziale o invalido non attivano alcuna regola e non bloccano la dettatura.

La pagina impostazioni rende esplicito che i testi selezionati vengono analizzati tramite OpenAI e permette di escludere sessioni e disattivare l'analisi. Sviluppa l'integrazione e verifica con fixture; l'attivazione del job sui testi reali avviene tramite scelta esplicita nell'app. Non usare account, chiavi o dati estranei al progetto.

Accettazione: proposte utili su casi preparati, input ostili trattati come testo, validazione reale e isolamento dei guasti. Le fixture non dimostrano qualità personale: dichiaralo nelle misure.

### D2 / VF-07: scheduler locale e gestione operativa

Eseguibile separato e LaunchAgent; fascia configurabile con default 02:30-06:00, fuso locale. Non legare il lavoro a una finestra aperta. Niente servizio web. Installa, aggiorna e disattiva in modo idempotente, mantenendo visibili stato e configurazione nell'app.

Una sola esecuzione, checkpoint persistenti, ripresa senza duplicazioni e transazioni brevi. Coordina il processo con il worker di dettatura: sospendi lavoro pesante e richieste ancora non avviate quando l'utente registra; gestisci cancellazione/termine delle operazioni già in corso. Alla scadenza non avviare altri lotti e interrompi in modo controllato il lavoro residuo. Una richiesta remota già accettata potrebbe comunque essere elaborata dal fornitore: non promettere annullamento del costo.

Prova avvio fuori fascia, crash, risveglio, cambio di fuso/ora legale, scadenza delle prenotazioni del job, perdita di rete e autenticazione scaduta. Retry limitati entro la fascia e successivo rinvio. Limiti configurabili di volume, tempo e richieste; mostra contatori reali senza inventare un costo monetario.

Accettazione: nessun doppio job, recupero coerente, nessun lavoro pesante fuori finestra, errori leggibili, dettatura funzionante anche con scheduler o analizzatore guasti.

### Trasversale / VF-08: qualità e prestazioni

Prepara un corpus iniziale di 20-30 dettature verificate se disponibili; altrimenti implementa raccolta e fixture e dichiara pendente la valutazione personale. Non inventare dati dell'utente. Separa esempi di proposta, calibrazione e valutazione per sessione.

Includi italiano, inglese, termini tecnici, frasi corrette, negazioni, ripetizioni intenzionali, nomi simili, numeri, comandi, frammentazione, revisione e sessioni lunghe. Misura correzioni utili, modifiche errate, interventi residui, memoria e latenza del processore e dell'intera dettatura separatamente. Registra macchina, versione, campioni e limiti.

Accettazione: confronto ripetibile fra regole disattivate e attive, anche sulla combinazione delle regole. Nessuna soglia di qualità o promessa di costo zero senza misure. Definisci e motiva gli obiettivi dopo la baseline, prima di ottimizzare.

### E / VF-09: esperimento di word boosting

È un esperimento obbligatorio di fattibilità, non un impegno a sostituire il decoder a ogni costo. Verifica NeMo-Speech.cpp o un'estensione compatibile con il motore attuale, supporto macOS, distribuzione, licenza e costo d'integrazione. Non installare un server o alterare il runtime principale solo per dimostrare una possibilità.

Confronta stato attuale, sole correzioni, solo boosting e combinazione. Usa un vocabolario limitato e frasi negative con parole simili. Calibra i pesi sul decoder; 5,5-6,5 non è vincolante. Conserva il percorso esistente come ripristino. Se servono audio dal vivo o un server, prepara il test e indica il requisito senza avviarli automaticamente.

Accettazione: report con prova ripetibile e decisione motivata, inclusi benefici, regressioni e limiti. Integra nel prodotto solo dopo un vantaggio misurato e senza cambiare il perimetro concordato. Un esito negativo documentato chiude l'esperimento, non autorizza a dichiarare che il boosting è stato implementato.

## Verifica e gestione dei lotti

Per ogni lotto esegui implementazione, una verifica distinta in sola lettura, riparazione dei problemi e nuova verifica. Usa un contesto indipendente quando il workflow e l'utente lo autorizzano; altrimenti una passata chiaramente separata. Non avviare sub-agenti automaticamente in assenza di autorizzazione applicabile. Non lasciare che chi verifica modifichi il codice durante la stessa verifica.

Ogni esito deve essere uno fra: completato con prove; controlli automatici passati con verifica manuale pendente; non approvato con difetti riproducibili. Non proseguire con lotti dipendenti da difetti irrisolti. Puoi avanzare su attività indipendenti mentre una prova manuale resta pendente, senza dichiarare completo il lotto precedente.

Controlli di base, adattati al diff:

```sh
pnpm desktop:check
pnpm desktop:ui
pnpm test:signing
cargo fmt --all --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
pnpm desktop:build
codesign --verify --deep --strict target/release/bundle/macos/VoiceFlow.app
```

Gli script `test:streaming` e `test:short-recordings` e i test ignorati non fanno parte dei controlli automatici da eseguire senza esaminarne effetti e requisiti. Nessuna riproduzione, registrazione o modifica del sistema audio implicita.

Aggiungi test mirati su database reali, migrazioni, conflitti di regole, fallimenti del fornitore, processi concorrenti e scheduler con orologio controllabile. Verifica la UI con build esistente o strumenti che non richiedano nuovi server. La build non sostituisce la prova della funzione reale; dichiara sempre cosa coprono i mock.

Per ogni lotto verificato crea un commit sul branch corrente con titolo riferito alla feature; registra SHA e prove. Mantieni separati cambi di comportamento e formattazione non necessaria. Se compare lavoro concorrente, non includerlo automaticamente nel commit del lotto.

## Consegna finale attesa

- Lotti A-D implementati e verificati, con eventuali prove manuali ancora pendenti nominate precisamente.
- Esito riproducibile del lotto E, oppure impedimento concreto documentato; non equiparare un blocco a un esperimento completato.
- UI di correzione, gruppi e suggerimenti; regole locali attive e reversibili; analisi e scheduler configurabili.
- Migrazioni, gestione degli errori, test e documentazione d'uso aggiornati fuori dai documenti congelati.
- Build macOS firmata, commit ordinati, stato del branch e riepilogo dei rischi residui.
- Registro di esecuzione completo, con distinzione fra sviluppato, provato con fixture e verificato nell'app reale.

## Fonti tecniche da riverificare all'integrazione

- NVIDIA: https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/asr/customization.md
- OpenAI: https://learn.chatgpt.com/docs/non-interactive-mode
- Progetto: `docs/dictionary.md`, `docs/history.md`, `docs/streaming-cleanup.md`, `docs/desktop.md` e documenti di verifica datati. Questi descrivono il prodotto e possono evolvere; non sostituiscono la baseline congelata.
