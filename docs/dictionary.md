# Dizionario e impostazioni

La barra laterale apre tre pagine: Dettatura, Dizionario e Impostazioni. Il collegamento alle impostazioni resta in fondo alla barra. Le pagine usano URL con hash, compatibili con il documento locale di Tauri.

La sessione di dettatura appartiene al layout principale. Cambiare pagina mantiene trascrizione, stato, comandi e permessi, con una sola sottoscrizione agli eventi nativi e un solo controllo periodico dei permessi. Durante una dettatura o un’elaborazione, le impostazioni del testo restano bloccate. La barra laterale permette di tornare ai controlli della sessione attiva.

## Dizionario

Le parole salvate non modificano ancora la dettatura. Non vengono inviate al modello né usate per correggere il testo.

Puoi aggiungere, modificare ed eliminare parole o espressioni e organizzarle in gruppi. Una parola può appartenere a più gruppi. Il filtro comprende tutte le parole, quelle senza gruppo e ogni gruppo creato; i conteggi dei gruppi indicano il totale, mentre il conteggio sopra la tabella segue anche la ricerca. Per rinominare o eliminare un gruppo, selezionalo nel filtro. Le eliminazioni richiedono conferma. Eliminare un gruppo conserva tutte le parole e le altre appartenenze.

Il dizionario nasce vuoto. Il database SQLite locale si trova in:

`~/Library/Application Support/VoiceFlow/dictionary.sqlite3`

Il file ha permessi `0600`. Non viene cancellato o sostituito se non è leggibile. Un errore viene mostrato nella pagina, con possibilità di riprovare; non impedisce l’avvio del servizio di dettatura. Chiudi VoiceFlow prima di copiare il file per un backup o di ripristinarlo.

Le parole accettano fino a 200 caratteri Unicode, i nomi dei gruppi fino a 80. Gli spazi iniziali e finali vengono rimossi; testo vuoto e caratteri di controllo vengono rifiutati. Le chiavi uniche usano il testo convertito in minuscolo Unicode: `Èquipe` ed `èquipe` sono duplicati. Non vengono applicate normalizzazione degli accenti o equivalenza fonetica.

## Impostazioni

La pagina raccoglie lingua, pulizia locale, formato del testo, stato e caricamento del modello, permessi macOS e scorciatoie. Le modifiche del testo mantengono il salvataggio automatico esistente in `desktop-settings.json`.

## Persistenza e verifiche

`src-tauri/src/dictionary.rs` possiede schema, validazione e transazioni. I comandi Tauri vengono eseguiti in background, separati dal worker audio; un mutex serializza letture e scritture. Ogni mutazione riguarda una sola parola o un solo gruppo e restituisce uno snapshot coerente. Le chiavi esterne proteggono le appartenenze e le transazioni annullano l’intera modifica in caso di errore. Il frontend valida le risposte IPC con Zod.

SQLite è incluso tramite `rusqlite` con la feature `bundled`, senza dipendere dall’installazione di SQLite sul Mac. La versione dello schema è registrata in `PRAGMA user_version`; uno schema più recente viene rifiutato.

Le prove automatiche del modulo coprono persistenza dopo riapertura, Unicode, duplicati, rollback, appartenenze multiple, eliminazioni, ID sconosciuti, input non valido, file corrotto e scritture concorrenti. Per eseguirle senza avviare l’app o usare l’audio:

```sh
cargo test -p voiceflow-desktop --locked dictionary::tests
pnpm desktop:check
pnpm desktop:ui
```
