# Pianificazione locale dell'analisi

In Impostazioni, salva prima il consenso in **Analisi con OpenAI**. In **Pianificazione locale**, seleziona l'attivazione del LaunchAgent e salva. I due consensi sono separati e inizialmente disattivati. L'approvazione delle proposte linguistiche resta manuale nella pagina Suggerimenti, salvo attivazione esplicita del criterio facoltativo per i nuovi rilievi con confidenza superiore a 90 e controlli locali superati. Aprire l'app o leggere lo stato non abilita il job.

La fascia iniziale è 02:30–06:00 nell'ora locale del Mac; 03:00–05:00 è una configurazione valida. La durata ammessa è da 1 minuto a 12 ore. Una fine precedente all'inizio indica il giorno seguente, per esempio 23:00–01:00. Inizio e fine uguali sono rifiutati. Il worker controlla nuovamente fascia e consenso prima della preparazione, prima della richiesta e durante il lavoro.

Il LaunchAgent `com.voiceflow.desktop.analysis` lavora nella sessione GUI dell'utente, anche dopo la chiusura della finestra o dell'app. Il Mac deve essere acceso e l'utente connesso. Non viene installato un servizio web. Il job usa l'eseguibile `Contents/MacOS/voiceflow-analysis` della build firmata corrente; il plist non contiene percorsi del checkout. In sviluppo, il controllo rifiuta l'installazione senza helper nel bundle.

## Stato e riparazione

L'app distingue preferenza richiesta, presenza del plist, rilevamento del job tramite `launchctl print`, configurazione del plist e proprietà del worker tramite lock del kernel. “Job caricato” significa registrato in launchd, non prova che un'analisi sia riuscita. I risultati, gli errori del provider e i contatori sono nella pagina Analisi, insieme agli ultimi avvii del job. La pagina rilegge automaticamente i dati durante il lavoro e quando torna in primo piano; il pannello delle Impostazioni mantiene il pulsante Aggiorna stato.

**Salva pianificazione** installa o aggiorna. **Ripara pianificazione** riconcilia una configurazione salvata dopo un errore di installazione o dopo lo spostamento del bundle. Entrambe le azioni sono idempotenti. **Disattiva pianificazione** salva OFF prima di chiamare `launchctl disable` e `bootout`. Se launchctl fallisce, OFF resta salvato e i processi rispettano la revoca. Ripetere la disattivazione completa la rimozione. Disattivare il consenso OpenAI interrompe comunque il worker, anche se il LaunchAgent resta registrato.

La riconciliazione e l'ispezione di launchctl avvengono fuori dal thread UI. Ogni comando ha un limite di due secondi; il lock di configurazione può attendere al massimo due secondi. Un errore conserva lo stato desiderato e viene mostrato insieme allo stato rilevato. Il percorso di dettatura non apre il database dello scheduler e non chiama launchctl.

## Fascia, risveglio e limiti

Il plist usa `StartCalendarInterval` all'inizio esatto della fascia e ogni cinque minuti al suo interno. Non usa `RunAtLoad`, `KeepAlive` o `StartInterval`. L'ora primaverile mancante non impedisce i successivi eventi validi della stessa fascia. L'ora autunnale ripetuta non ripresenta le fonti già completate: checkpoint, lock e budget restano persistenti. Se la fascia cade interamente nell'ora mancante, quel giorno non c'è lavoro.

Il manuale locale `launchd.plist(5)` e la [documentazione Apple sui job pianificati](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html) spiegano che gli eventi calendario persi durante il sonno vengono riuniti al risveglio, mentre `StartInterval` perde il proprio evento. Il worker ricontrolla sempre la fascia: un risveglio dopo la fine non autorizza analisi. La conversione in ora locale usa Chrono, che rivaluta il fuso locale con una cache di circa un secondo. Non cambia orologio o fuso del sistema.

Ogni avvio esegue al massimo un tentativo D1. Dopo un errore o un'interruzione, il retry attende almeno cinque minuti e resta nella fascia; dopo tre tentativi falliti o interrotti nello stesso giorno UTC rinvia al giorno seguente. Un nuovo avvio dopo il limite può ispezionare lo stato ma non invia richieste. Restano vincolanti anche i limiti D1 di fonti, byte, tentativi e secondi: i default D1 consentono due tentativi totali al giorno. I contatori sono tentativi e tempi reali, non costi monetari stimati.

Un arretramento dell'orologio non anticipa il prossimo retry persistito. Il timeout della richiesta e il tetto del worker usano anche tempo monotono; un salto di orologio non concede lavoro illimitato. Un tentativo interrotto bruscamente conserva la prenotazione D1 massima, senza inventare secondi misurati. La scadenza UTC mostrata nell'app è informativa: la sua scadenza da sola non autorizza un secondo worker.

## Proprietà dei processi e dettatura

I file di lock sono stabili e non vengono cancellati. `flock` lega la proprietà alle descrizioni di file aperte, non a un booleano UI, a un PID salvato o alla scadenza di una riga SQLite. Il launcher possiede il lock del worker; executor, watchdog e provider ne ereditano il descrittore. Executor, watchdog e provider ereditano anche il permesso del lavoro. I ruoli interni verificano il descrittore ereditato e l'inode del lock prima di procedere.

L'executor ha un gruppo di processi dedicato. Il provider D2 resta in quel gruppo; il provider D1 usato separatamente conserva il proprio gruppo. Il launcher controlla l'executor senza raccoglierne subito lo stato, mediante `waitid(WNOWAIT)`, quindi termina il gruppo prima di `wait`. Il PID del figlio non può essere riutilizzato fra osservazione e pulizia. Non esistono kill basati su PID/PGID letti da un file persistente.

Una pipe rileva la morte del launcher; una seconda rileva la morte dell'executor. Il watchdog controlla anche fascia, consenso e intenzione di dettare. Alla preemption lascia fino a 250 ms per la cancellazione cooperativa e poi termina il gruppo. Il launcher offre un secondo controllo con 350 ms di grazia. Letture SQLite e polling hanno limiti; il percorso di cattura attende il permesso per un massimo di 1,5 secondi, più fino a 100 ms per l'intenzione.

Il worker di dettatura prende l'intenzione esclusiva prima di costruire il decoder e avviare ASR. Mantiene intenzione e permesso attraverso pausa, ripresa, finalizzazione e distruzione di capture/decoder, anche su errore e cancellazione. Il provider può partire solo mentre possiede il permesso di lavoro. La cattura può iniziare normalmente solo dopo che quel permesso è libero. Un crash dell'app rilascia i lock nel kernel, senza un indicatore persistente da azzerare.

Un guasto del database dello scheduler o di launchctl non blocca la cattura. Se tutti i controllori vengono sospesi contemporaneamente, oppure il sistema non concede loro CPU entro il limite, non si può garantire la preemption entro 1,5 secondi. In questo caso la dettatura continua con un avviso e mantiene l'intenzione di fermare l'analisi finché dura la sessione. La verifica automatica copre guasti singoli di processo, inclusi SIGSTOP, non promette esclusione assoluta nel caso simultaneo descritto.

Una richiesta già accettata da OpenAI può continuare sul fornitore dopo la terminazione dei processi locali. VoiceFlow non promette annullamento del costo o cancellazione dei dati remoti.

## Stato locale e API

`analysis-scheduler.sqlite3` conserva configurazione, fase, scadenza informativa, retry e ultimo errore. Non conserva trascrizioni o risposte. I checkpoint e i budget D1 restano in `history.sqlite3`. Le transazioni SQLite sono brevi; nessuna transazione resta aperta durante launchctl o il provider.

Il launcher rimuove la directory privata `analysis-runtime` dopo aver terminato il gruppo. Un crash del launcher può lasciare file temporanei CLI; il successivo avvio del helper o la lettura dello stato nell'app li rimuove soltanto dopo aver acquisito il lock del worker. Non legge il contenuto e non cancella la directory di un worker vivo. Le directory di test contengono solo fixture. I controlli CLI D1 riducono conservazione e logging ma il comportamento effettivo remoto richiede ancora la prova autorizzata della CLI.

IPC asincroni, entrambi eseguiti con `spawn_blocking`:

- `scheduler_status()`: configurazione, installazione/rilevamento, corrispondenza del plist, helper disponibile, proprietà del worker, dettatura, fase, lease, retry ed errore. Può rimuovere lo scratch abbandonato dopo prova di assenza del worker.
- `scheduler_configure({ schedule: { enabled, startMinute, endMinute }, consent })`: valida durata e consenso esplicito, richiede il consenso D1 già salvato e l'helper nel bundle per abilitare, salva e riconcilia. La risposta può contenere `lastError` con preferenza già salvata dopo un fallimento launchctl.

Il comando interno del helper riceve `--data` dal plist. Ruoli executor/watchdog richiedono descrittori ereditati e un gruppo valido. Il controllo d'orologio e il ruolo di cattura finta esistono soltanto sotto `cfg(test)`, non nel binario distribuito.

## Consegna e prova manuale pendente

`pnpm desktop:build` compila il helper release con `--locked`, genera il bundle, copia il helper in `Contents/MacOS`, firma il helper con identificatore `com.voiceflow.desktop.analysis` e la stessa identità stabile, poi rifirma e verifica il bundle. La build finale firmata viene verificata dopo il lotto E.

La prova nativa richiede l'attivazione esplicita nell'app e resta da eseguire:

1. Aprire la build finale firmata, verificare entrambi i consensi OFF e helper presente. Non salvare consensi per controllare soltanto l'interfaccia.
2. Per autorizzare il job reale, selezionare prima il consenso OpenAI e poi l'attivazione della pianificazione. Configurare una fascia breve che includa l'ora corrente e limiti D1 ridotti.
3. Chiudere la finestra e l'app, attendere il prossimo evento calendario. Riaprire e verificare tentativo, checkpoint, stato ed eventuali errori. Con il criterio automatico disattivato non devono essere pubblicate regole; se esplicitamente attivato, verificare che soltanto i nuovi rilievi sopra 90 con controlli locali superati possano pubblicare.
4. Solo durante una prova audio esplicitamente richiesta, avviare dettatura mentre il provider lavora. Controllare preemption, pausa/ripresa/finalizzazione e avviso eventuale; verificare che la dettatura resti utilizzabile.
5. Verificare fine fascia e revoca del consenso durante il lavoro. Nessuna nuova richiesta dopo il confine; i dati già inviati restano soggetti alla policy del fornitore.
6. Salvare 03:00–05:00 due volte, riparare due volte e disattivare due volte. Confrontare preferenza, plist e rilevamento. Non modificare fuso/ora del Mac per questa prova; le transizioni sono coperte da clock controllato.
7. Dopo uno spostamento autorizzato del bundle, usare Ripara e verificare il nuovo percorso. Ripetere la verifica della firma di helper e app.

L'interfaccia è desktop. La dimensione minima dichiarata è 780×620; la navigazione per smartphone non rientra in questo lotto.


## Avvii visibili anche senza richieste

La pagina Analisi distingue consenso OpenAI, preferenza di pianificazione, plist installato, job caricato e worker attivo. Mostra il motivo per cui l'analisi automatica non è attiva e collega le Impostazioni per intervenire. L'assenza di analisi completate non viene interpretata come un guasto di launchd: le due cronologie restano separate.

`analysis-scheduler.sqlite3` conserva gli ultimi 100 avvii del launcher che hanno acquisito il lock del worker. La conservazione limitata evita di accumulare indefinitamente i controlli senza lavoro. Gli avvii concorrenti che non acquisiscono il lock non aggiungono una riga. I tentativi effettivi del provider rimangono integralmente nello storico di Analisi, senza questa scadenza.

Ogni avvio registrato conserva l'inizio, la fine se osservata, un codice statico e l'eventuale ID del lotto. Gli esiti distinguono pianificazione disattivata, fuori fascia, dettatura, consenso assente/non leggibile, attesa del retry, nessuna fonte preparabile, limite giornaliero, CLI indisponibile, errore del worker e risultato del tentativo. «Nessuna fonte preparabile» può dipendere anche da esclusioni o fonti troppo grandi; non equivale a una chiamata completata senza candidati. Un errore prima dell'apertura dell'archivio può impedire anche la registrazione diagnostica.

Il nuovo IPC `scheduler_invocations()` usa `spawn_blocking`. Se acquisisce il lock del worker, può riconciliare vecchi avvii rimasti aperti e i relativi tentativi come interrotti. Anche il launcher riconcilia prima di registrare il prossimo avvio. Non inventa l'ora della fine o il tempo misurato. Il launcher recupera inoltre i tentativi dopo aver terminato e raccolto il proprio gruppo di processi. La prova resta il lock del kernel, non la scadenza informativa né il PID persistito.

Le nuove righe diagnostiche non contengono testo del provider, stdout/stderr, prompt o trascrizioni. Le scritture usano lo stesso timeout SQLite breve dello scheduler e non entrano nel percorso della cattura. I limiti D1, i lock, la fascia, il backoff e le regole di cancellazione restano quelli descritti sopra. La CLI reale e la consegna del bundle firmato richiedono la verifica nativa separata; gli esiti dei test con provider finti non vengono importati nello storico personale.
