# VoiceFlow | Piano di sviluppo v1.0

Riferimento congelato · 25 settembre 2026 · VF-2026-09-25-V1

## 1. Obiettivo e decisioni

Rendere operativo il dizionario e migliorare progressivamente la pulizia del testo attraverso correzioni confermate e analisi notturna. La dettatura deve rimanere locale e rapida. Questo documento fissa il perimetro; il prompt allegato descrive come eseguirlo.

### Base esistente

App macOS Rust/Tauri, React/TypeScript e shadcn/ui; Nemotron 3.5 tramite parakeet-rs. Buffer di sette parole modificabili, testo consolidato, comandi di formattazione, dizionario a gruppi e storico SQLite. Il dizionario non influenza ancora la dettatura. Lo storico conserva il testo finale, non l'originale del riconoscitore.

Il filtro dei marcatori è presente e passa i test automatici, ma l'utente segnala ancora <unk>: la verifica nell'app reale resta aperta. Conservare la gestione attuale di coda audio, stop, pausa, incolla, Bluetooth e firma. La baseline esatta e le verifiche sono in BASELINE.md.

### Decisioni fissate

- Prima correzioni deterministiche e contestuali; boosting in un esperimento successivo.
- Nessuna conservazione audio, riaddestramento della voce, riscrittura generale o modello Jev in questa versione.
- Un vocabolario SQLite con gruppi sovrapponibili. Selezione manuale del contesto; termini e regole caricati in RAM per sessione.
- Analisi notturna configurabile: impostazione iniziale 02:30-06:00, ora locale. Deve poter diventare 03:00-05:00.
- Individuazione automatica di nuove parole e varianti di pulizia; attivazione iniziale solo dopo approvazione nell'app.
- Regole versionate, disattivabili e reversibili. Nessuna modifica delle regole di una dettatura già iniziata.

### Confronto delle due strade

**Correzioni contestuali:** risultato prevedibile sulle varianti note, sforzo medio, fattibilità alta. Non recuperano automaticamente qualsiasi parola mai vista.

**Word boosting:** può prevenire errori durante il riconoscimento; sforzo alto e fattibilità da misurare sul Mac e sul runtime. Le due strade sono complementari. Il valore 5,5-6,5 non è un requisito: i pesi dipendono dal decoder.

### Versione immutabile

PLAN.md, EXECUTION_PROMPT.md, BASELINE.md e il PDF sono congelati mediante commit e manifest SHA-256. Non aggiornarli durante l'esecuzione. Registrare avanzamento e scostamenti altrove; nuove decisioni producono una v2 esplicita. Il commit identifica la base, il manifest identifica gli allegati.

<!-- page -->

## 2. Lotti A-B | Affidabilità e correzioni personali

### VF-01 · Marcatori nella dettatura · Priorità P0

**Necessità:** verificare la segnalazione su <unk> nell'app effettivamente utilizzata, distinguendo versione eseguita e vecchie righe dello storico.

**Logica:** riprodurre prima il problema; seguire il testo fra decoder, anteprima, finale, copia/incolla e salvataggio. Conservare la policy già richiesta di rimozione delle sequenze complete <...>, comprese quelle frammentate. Non migrare automaticamente lo storico preesistente. Documentare l'effetto sui contenuti letterali fra parentesi angolari.

**Accettazione:** nessun marcatore completo nei nuovi risultati; nessuna perdita di parole o separatori; pausa, stop e revisioni coerenti. Test automatici e riscontro nella build realmente usata. Sforzo iniziale contenuto, causa ancora da determinare.

### VF-02 · Storico correggibile · Priorità P1

**Necessità:** esempi affidabili che distinguano errore ASR, effetto della pulizia e preferenza stilistica.

**Logica:** per le nuove sessioni conservare originale ASR, risultato elaborato, correzione confermata, lingua, gruppi e versioni applicate. Aggiungere modifica del testo e azione per ricordare una correzione. Originale immutabile; revisioni tracciate. Le righe precedenti dichiarano l'originale non disponibile. Nessuna cattura delle modifiche fatte in altre app.

**Accettazione:** riapertura e migrazione SQLite senza perdita dei dati; correzioni annullabili; niente doppio conteggio delle sessioni. Le statistiche originarie restano riferite alla dettatura prodotta. La cancellazione comprende i dati derivati secondo una policy esplicita. Sforzo medio.

### VF-03 · Dizionario operativo · Priorità P1

**Necessità:** associare ai termini grafia preferita, varianti confermate, lingua, gruppi, condizioni ed eccezioni.

**Logica:** snapshot in memoria a inizio sessione; confronto su parole complete ed espressioni supportate dal buffer. Precedenza alla regola più specifica; conflitti irrisolti conservano l'originale. Un termine privo di varianti non abilita sostituzioni fonetiche arbitrarie. Nessuna query SQLite o chiamata remota per ogni parola.

**Accettazione:** correzioni ripetibili, contesto rispettato, nessuna riscrittura a cascata; preservazione di nomi, negazioni, numeri, URL e codice fuori dalle sostituzioni esplicite. Espressioni troppo lunghe per la finestra non vengono troncate né applicate parzialmente. Sforzo medio, fattibilità alta.

**Dipendenze:** VF-02 e VF-03 condividono gli esempi confermati. Integrare una sola responsabilità per commit, preservando navigazione e sessione attiva.

<!-- page -->

## 3. Lotto C | Regole estendibili e suggerimenti

### VF-04 · Catalogo di pulizia · Priorità P1

**Necessità:** aggiungere varianti simili a "il la scheda" e "della dell'acquisizione" senza riscrivere il programma per ogni caso.

**Logica:** regole dichiarative separate dai termini del dizionario. Ogni regola contiene un'operazione supportata, condizioni, eccezioni, lingua, esempi e versione. Il motore lavora sull'originale della finestra, con precedenze definite e senza applicare ricorsivamente le proprie sostituzioni.

Le nuove operazioni devono essere limitate e verificabili. Nessun codice arbitrario generato dal modello. Comandi e sostituzioni devono attraversare correttamente i confini della finestra; pausa e stop completano soltanto i segmenti disponibili.

**Accettazione:** vecchie regole ancora corrette; nuove varianti supportate; ripetizioni intenzionali, negazioni e frasi ambigue preservate. "La dispositivo" e ripensamenti estesi restano fuori perimetro. Sforzo medio.

### VF-05 · Gestione dei suggerimenti · Priorità P1

**Necessità:** rendere comprensibile e controllabile ogni proposta prima che modifichi le dettature.

**Logica:** pagina con prima/dopo, motivazione, gruppo, esempi reali, sessioni di origine e conflitti. Azioni: approva, modifica, rifiuta, rimanda, disattiva. Distinguere ipotesi ricavate dal testo da correzioni confermate dall'utente. Stati minimi: proposta, approvata, attiva, rifiutata, disattivata.

L'approvazione pubblica atomicamente una nuova versione, disponibile dalla sessione successiva. Le proposte ripetute vengono raggruppate; un rifiuto non deve riapparire identico ogni notte. Conservarne la motivazione e riaprirlo solo con nuove evidenze identificabili.

**Accettazione:** nessuna attivazione silenziosa; origine e stato visibili; ripristino della versione precedente senza alterare lo storico; conflitti bloccano la pubblicazione. Sforzo medio.

### Miglioramento continuo previsto

L'analisi in background deve cercare sia varianti di termini sia nuove famiglie di pulizia. Raggruppare esempi comparabili, proporre la regola più circoscritta utile e provarla anche su testi che devono restare invariati. La frequenza da sola non dimostra un errore.

### Confine della prima versione

Rilevamento e preparazione delle proposte sono automatici. La pubblicazione richiede approvazione nell'app. Attivazione automatica e selezione automatica dei gruppi sono sviluppi successivi, non scorciatoie per dichiarare completo questo lotto.

**Dipendenze:** VF-04 usa il motore di VF-03; VF-05 serve anche per rivedere le proposte notturne di VF-06.

<!-- page -->

## 4. Lotto D | Analisi notturna e processi separati

### VF-06 · Analisi di termini e pulizia · Priorità P1

**Necessità:** elaborare nuove trascrizioni, correzioni confermate e suggerimenti aperti senza pesare sulla dettatura.

**Logica:** preparazione locale di lotti testuali; Codex CLI come analizzatore sostituibile; risultato strutturato validato dall'app. Raggruppare errori, proporre parole, varianti, condizioni ed eccezioni e individuare regole che l'utente continua a correggere. Frequenze e riferimenti alle sessioni sono verificati dal backend.

Trattare la trascrizione come dato, anche quando contiene istruzioni. Il modello propone dati: non modifica codice, database delle regole attive o configurazioni. In caso di risposta invalida, nessuna pubblicazione. L'analisi con OpenAI invia i testi selezionati al servizio; questa modalità deve essere esplicita e disattivabile nelle impostazioni.

**Accettazione:** proposte tracciabili, distinte per termini e pulizia; niente evidenze inventate, duplicazioni o apprendimento dalle sole sostituzioni automatiche. Limiti di volume e durata configurabili. Sforzo medio-alto; utilità da misurare.

### VF-07 · Scheduler e recupero · Priorità P1

**Necessità:** eseguibile dedicato, avviabile senza la finestra dell'app, con stato persistente e visibile.

**Logica:** LaunchAgent macOS; default 02:30-06:00, ora locale. Una sola esecuzione; checkpoint per riprendere il lavoro senza duplicati. Stop del lavoro pesante alla fine della fascia e durante una dettatura. Non basta una notifica della UI: coordinamento affidabile fra processi. Dopo crash, verificare l'effettivo stato dell'app e recuperare gli impegni scaduti.

Gestire Mac spento, risveglio, cambio di fuso/ora legale, rete assente e autenticazione scaduta. Un avvio tardivo fuori fascia rimanda l'analisi. Nessun retry continuo. Installazione, aggiornamento e disattivazione della schedulazione devono essere ripetibili.

**Accettazione:** nessuna sovrapposizione, ripresa verificata dopo interruzione, nessuna elaborazione pesante fuori fascia, errori visibili e dettatura indipendente dal servizio. Sforzo medio.

### Ripartizione delle responsabilità

- Worker della dettatura: ASR e regole locali già approvate, con snapshot stabile per sessione.
- Persistenza: originali, risultati, feedback, regole e stato dei lavori in SQLite, con transazioni brevi e compatibilità delle migrazioni.
- Processo notturno: raccolta, analisi e proposte. Nessuna modifica diretta alle regole attive.
- Interfaccia: correzioni, scelta dei gruppi, revisione, orari, stato ed errori.

**Dipendenze:** VF-06 richiede VF-02, VF-04 e VF-05; VF-07 ne governa l'esecuzione. Nessun hosting esterno necessario per archivio o scheduler.

<!-- page -->

## 5. Lotto E | Misure, boosting e consegna

### VF-08 · Misurazione e regressioni · Trasversale

Preparare da VF-02 un insieme iniziale di 20-30 dettature verificate, ampliabile, con italiano, inglese, termini tecnici e frasi già corrette. Separare gli esempi usati per proporre le regole da quelli usati per valutarle. Dati sintetici possono verificare il software, non provare qualità sul parlato personale.

Misurare correzioni riuscite, modifiche sbagliate, interventi manuali residui, latenza aggiunta e memoria. Versionare il confronto. Nessuna percentuale di accuratezza dedotta dal solo numero di sostituzioni. La prova deve includere l'effetto combinato delle regole.

### VF-09 · Esperimento di word boosting · Priorità P2

Verificare integrazione del runtime/decoder e compatibilità macOS prima di impegnarsi nella sostituzione. Confrontare motore attuale, correzioni sole, boosting solo e combinazione sullo stesso vocabolario ristretto. Usare anche parole simili che non devono essere favorite.

La compatibilità dichiarata da NVIDIA rende l'ipotesi concreta, ma non garantisce il risultato sul Mac. Misurare qualità, memoria, latenza, avvio e distribuzione dell'app. Non fissare a priori il peso 5,5-6,5. Conservare il runtime attuale come ripristino. Sforzo alto; fattibilità inizialmente incerta.

**Esito richiesto:** prova riproducibile e decisione motivata. Integrare solo se il vantaggio è verificato; un esito negativo documentato completa l'esperimento, non equivale a boosting consegnato. Nessun nuovo server o audio reale senza richiesta esplicita dell'utente.

### Sequenza e criteri di completamento

**A:** VF-01. **B:** VF-02 e VF-03. **C:** VF-04 e VF-05. **D:** VF-06 e VF-07. **E:** VF-09. VF-08 accompagna B-E.

Per ogni lotto: baseline, implementazione circoscritta, verifica separata in sola lettura, eventuale riparazione, nuova verifica e commit. Non passare un lotto incompleto come concluso. Se manca una prova manuale, indicare esattamente cosa rimane pendente e continuare solo attività indipendenti.

Consegna finale: migrazioni compatibili; test funzionali e di errore; controlli Rust/TypeScript; build firmata; verifica di sessione, copia/incolla, storico e regole; scheduler con recupero; report qualità con limiti espliciti. Nessun avvio automatico di server, microfono, riproduzione audio o cambio del dispositivo audio.

### Riferimenti e tracciamento

Il prompt eseguibile è EXECUTION_PROMPT.md; la baseline è BASELINE.md. Usare un registro separato docs/execution/voiceflow-improvement-v1.md, aggiornabile con prove, commit e decisioni. Ogni modifica di perimetro richiede una nuova versione del piano.

Fonti consultate il 25 settembre 2026: documentazione NVIDIA NeMo-Speech.cpp, docs/asr/customization.md; documentazione OpenAI, learn.chatgpt.com/docs/non-interactive-mode. I collegamenti completi sono nel prompt. Ricontrollare le capacità esterne al momento dell'integrazione.
