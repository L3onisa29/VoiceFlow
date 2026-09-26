# Analisi consultabili e confidenza dei rilievi

Addendum del 26 settembre 2026 alle istruzioni approvate di VoiceFlow. Registra
le nuove richieste esplicite dell'utente senza modificare i riferimenti congelati
VF-2026-09-25-V1. L'esecuzione è tracciata in
[voiceflow-improvement-v1.md](../execution/voiceflow-improvement-v1.md).

## Richieste e sequenza

1. Rendere consultabile ogni futura analisi nell'app, con dettaglio delle fonti,
   risultati, proposte ed errori. Conservare distinti i tentativi e i loro esiti,
   anche dopo un retry. Mostrare perché un avvio pianificato non ha prodotto
   un'analisi, senza confondere job registrato, worker in corso e analisi riuscita.
2. Verificare e attivare la pianificazione locale. L'utente ha confermato
   esplicitamente l'invio a OpenAI tramite Codex CLI dei testi e delle correzioni
   delle sessioni non escluse, nella fascia locale 02:30–06:00.
3. Per i nuovi rilievi mostrare nome, testo, motivazione, azione consigliata e
   confidenza da 0 a 100. Includere la decisione di mantenere il testo. Elaborare
   in lotti limitati e rendere consultabili gli esiti per esecuzione.
4. Approvare automaticamente i rilievi con confidenza superiore a 90; mantenere
   una revisione manuale graduata sotto tale soglia. Questa istruzione sostituisce
   il precedente requisito di approvazione manuale universale nel solo perimetro
   sopra soglia.

La cronologia viene implementata e verificata prima del lotto dipendente su
confidenza e pubblicazione. Ogni lotto ha implementazione e nuova verifica
indipendente, con eventuale riparazione e nuova revisione.

## Significato del punteggio e pubblicazione

La confidenza è una stima del provider sull'azione consigliata, non una misura
calibrata dell'accuratezza del riconoscimento. Il valore è intero, fra 0 e 100.

| Valore | Stato | Trattamento |
| --- | --- | --- |
| 91–100 | Molto alta | Approvazione automatica se applicabile e valida |
| 75–90 | Alta | Revisione manuale |
| 50–74 | Media | Revisione manuale |
| 0–49 | Bassa | Revisione manuale |
| Assente nei dati precedenti | Non disponibile | Nessuna approvazione automatica |

Il valore 90 non abilita l'approvazione automatica. Una decisione di mantenere il
testo non pubblica una regola vuota. Le altre decisioni possono pubblicare solo
operazioni supportate e validate, con fonti verificabili e senza conflitti.
La stima elevata non scavalca rifiuti, rinvii o disattivazioni manuali già salvati.
Ogni pubblicazione conserva provenienza, esito, versione e possibilità di
disattivazione/ripristino; non altera una dettatura già iniziata.

## Criteri di verifica

Migrazioni compatibili e riapertura; esiti dei retry distinti; paginazione stabile;
collegamenti esatti alle fonti e alle proposte; cancellazione/esclusione rispettate;
nessun payload o output grezzo del provider duplicato nei log. La cronologia dei
tentativi resta persistente; la diagnostica degli avvii inattivi può avere una
conservazione limitata dichiarata nell'interfaccia.

Per la confidenza: prove dei confini 0/49/50/74/75/90/91/100, valori invalidi o
assenti, mantenimento del testo, regole in conflitto, retry idempotenti, rispetto
delle scelte manuali, revoca del consenso ed esito visibile della pubblicazione.
Le prove usano prima SQLite reale temporaneo, provider fixture e UI senza server;
il job macOS reale viene verificato separatamente. Nessuna registrazione audio
o attivazione remota fuori dalla fascia autorizzata è implicita.
