# Dizionario e impostazioni

La barra laterale apre tre pagine: Dettatura, Dizionario e Impostazioni. Il collegamento alle impostazioni resta in fondo alla barra. Le pagine usano URL con hash, compatibili con il documento locale di Tauri.

La sessione di dettatura appartiene al layout principale. Cambiare pagina mantiene trascrizione, stato, comandi e permessi, con una sola sottoscrizione agli eventi nativi e un solo controllo periodico dei permessi. Durante una dettatura o un’elaborazione, le impostazioni del testo restano bloccate. La barra laterale permette di tornare ai controlli della sessione attiva.

## Dizionario

Il dizionario corregge soltanto le varianti che confermi nella pagina. Apri una parola, inserisci la grafia preferita e le varianti riconosciute da sostituire, una per riga, poi premi **Conferma varianti e salva**. Un termine senza varianti rimane un promemoria e non autorizza corrispondenze inventate. Le correzioni ricordate nello storico sono esempi separati e non diventano regole automaticamente.

### Gruppi e sessioni

Una parola può appartenere a più gruppi. Seleziona manualmente i **Gruppi attivi nella prossima dettatura**. I termini senza gruppo sono sempre disponibili; gli altri richiedono almeno un gruppo selezionato. Il filtro della tabella serve solo a trovare e gestire parole, non attiva i gruppi. Eliminare un gruppo conserva le parole e le altre appartenenze, rimuove la sua selezione e rende globali i termini rimasti senza gruppi.

All'avvio di una nuova dettatura, VoiceFlow legge una sola volta uno snapshot coerente dal database e compila le varianti in RAM. Cambi a parole, gruppi e selezione si applicano alla sessione successiva. Pausa e ripresa usano lo stesso snapshot. Lo storico registra i gruppi selezionati con ID e nome, la versione del dizionario e quella del processore. La versione identifica la configurazione usata; il database B2 non conserva copie storiche di tutte le configurazioni precedenti.

### Corrispondenze, condizioni e conflitti

- La corrispondenza esatta distingue maiuscole e usa un singolo spazio tra le parole. L'opzione **Ignora maiuscole Unicode** converte esplicitamente in minuscolo Unicode e accetta separatori di spazi; non attraversa un ritorno a capo. Non corregge accenti, apostrofi o somiglianze fonetiche.
- Il confronto usa parole intere. `voice flow` può diventare `VoiceFlow`, mentre `voice flower`, `https://voice.flow` e `x_voice_flow` restano invariati. La punteggiatura terminale `, . : ; ! ?` viene conservata. Apostrofo diritto e tipografico sono distinti: aggiungi entrambe le varianti se le vuoi autorizzare.
- Le lingue Italiano e Inglese seguono la lingua scelta nelle impostazioni. Con lingua automatica si applicano soltanto le regole **Tutte**, senza indovinare il contesto linguistico.
- **Solo dopo** e **Solo prima di** richiedono una parola adiacente nell'originale. Le eccezioni escludono il contesto indicato. Ogni condizione o riga di eccezione contiene una sola parola. Il confronto segue l'opzione esatta o Unicode della regola e comprende la punteggiatura scritta nel campo. Le condizioni non attraversano pausa o fine segmento.
- Ogni variante contiene da una a sette parole. Se usa una condizione o eccezione successiva, variante e parola successiva devono rientrare nelle sette parole. Il modulo rifiuta le espressioni oltre il limite, senza spezzarle. Massimo 128 varianti per termine, 32 eccezioni per lato e 4096 varianti applicabili per lingua, considerando tutti i gruppi. La grafia preferita accetta fino a 200 caratteri e non può introdurre parentesi angolari riservate al filtro dei marcatori.
- Vince prima la frase più lunga, poi il maggior numero di condizioni richieste, la lingua specifica, il gruppo specifico e infine la corrispondenza esatta. Le eccezioni escludono una regola prima del confronto. Se due regole applicabili hanno lo stesso rango e risultati diversi, l'intera frase originale resta invariata. La pagina evidenzia anche conflitti potenziali tra gruppi selezionabili insieme. Modifica variante, condizione, lingua o appartenenza per risolverli.

I comandi parlati hanno precedenza quando il formato li abilita. Nessuna variante può assorbire una parte di un comando riconosciuto. Le sostituzioni terminologiche vengono decise sull'originale della finestra, prima della pulizia locale. Un risultato sostituito non viene passato nuovamente al dizionario, ai comandi o alla pulizia. Negazioni, numeri, nomi, ripetizioni volute, URL e codice fuori dalle corrispondenze esplicite restano invariati.

Il processore conserva sette parole rivedibili dal decoder. Per decidere una frase lunga senza consolidarla troppo presto trattiene anche un contesto sorgente già congelato, fino a quindici parole complete totali contando il contesto sinistro. Il numero è limitato e indipendente dalla durata della dettatura. Non consulta SQLite né servizi remoti per ogni parola.

### Persistenza

Il dizionario nasce vuoto. SQLite locale si trova in `~/Library/Application Support/VoiceFlow/dictionary.sqlite3`, con permessi `0600`. La migrazione v2 conserva i termini precedenti senza inventare varianti e parte senza gruppi selezionati. Le transazioni proteggono testo, appartenenze, selezione e versione; un errore annulla la modifica.

Un database non leggibile non viene sostituito. La pagina mostra l'errore e permette di riprovare. Se non è disponibile all'avvio della dettatura, la sessione continua senza regole terminologiche, con un avviso e senza una versione di dizionario nei metadati. Chiudi VoiceFlow prima di copiare il database per backup o ripristino.

I nomi dei gruppi accettano fino a 80 caratteri. Le chiavi dei termini e gruppi usano il minuscolo Unicode per evitare duplicati come `Èquipe` ed `èquipe`. Non viene applicata normalizzazione degli accenti.

## Impostazioni

La pagina raccoglie lingua, pulizia locale, formato del testo, stato e caricamento del modello, permessi macOS e scorciatoie. Lingua, pulizia locale e formato vengono salvati automaticamente nel database SQLite `~/Library/Application Support/VoiceFlow/desktop-settings.sqlite3`, con permessi `0600`.

Alla prima apertura del database, l'app importa le preferenze da `desktop-settings.json`, se presente, e conserva il file originale. L'importazione e la versione dello schema vengono salvate nella stessa transazione; le aperture successive leggono solo SQLite. Un JSON non leggibile impedisce l'importazione e può essere corretto per ritentare. Un database non leggibile o con schema più recente non viene sostituito. Il servizio segnala l'errore e può continuare la dettatura con le preferenze predefinite in memoria.

Le preferenze restano locali e funzionano offline. Esportazione/importazione del profilo e sincronizzazione automatica tra dispositivi non sono ancora implementate.

Per l'uso futuro su più dispositivi, il requisito è una sincronizzazione automatica che non blocchi registrazione o trascrizione offline. Nell'attuale uso su un solo Mac, un profilo esportabile/importabile può essere un primo passo; il servizio di sincronizzazione e il relativo fornitore restano da definire.

## Persistenza e verifiche

`src-tauri/src/dictionary.rs` possiede schema, validazione e transazioni. I comandi Tauri vengono eseguiti in background, separati dal worker audio; un mutex serializza letture e scritture. Ogni mutazione riguarda una parola, un gruppo o la selezione dei gruppi e restituisce uno snapshot coerente. Le chiavi esterne proteggono le appartenenze e le transazioni annullano l’intera modifica in caso di errore. Il frontend valida le risposte IPC con Zod.

SQLite è incluso tramite `rusqlite` con la feature `bundled`, senza dipendere dall’installazione di SQLite sul Mac. La versione dello schema è registrata in `PRAGMA user_version`; uno schema più recente viene rifiutato.

Le prove automatiche coprono migrazioni e rollback su SQLite reale, riapertura, snapshot immutabili, lingue e gruppi, conflitti, Unicode, duplicati, eliminazioni, input non valido, file corrotto e scritture concorrenti. Le prove del processore coprono frasi di sette parole, revisioni, frammentazione, pause, comandi, conservazione del testo e assenza di sostituzioni ricorsive. Per eseguirle senza avviare l’app o usare l’audio:

```sh
cargo test -p voiceflow-desktop --locked dictionary::tests
cargo test -p voiceflow-core --locked dictionary::tests
pnpm desktop:check
pnpm desktop:ui
```

Le revisioni sono archiviate da C2 in `dictionary_versions`, dalla configurazione presente al primo aggiornamento in avanti. Le regole terminologiche approvate nella pagina Suggerimenti vengono combinate con il dizionario manuale nello snapshot della sessione. La pubblicazione verifica anche i conflitti e gli esempi di queste regole; un ripristino dei suggerimenti non annulla le modifiche manuali al dizionario. Contratti e limiti in [Suggerimenti](suggestions.md).
