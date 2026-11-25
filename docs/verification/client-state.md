# Stato client: Zustand e TanStack Query

Verifica completata il 27 settembre 2026: build e TypeScript superati, tutti i 9 gruppi di `pnpm test:ui` superati. Review indipendenti approvate sia per Zustand sia per Query dopo la riparazione della race sulla prima lettura. Nessun blocker residuo nell'ambito frontend verificato.

## Ambito

Zustand proietta in memoria lo stato della dettatura ricevuto da Rust. Rust mantiene la responsabilità della sessione e delle preferenze persistite. `useDictation` richiede un selector e applica `useShallow`; `useDictationState` seleziona solo i campi necessari. La connessione agli eventi resta nella root, indipendente dalle route.

TanStack Query gestisce le letture Tauri di dizionario, suggerimenti, cronologia, dettagli dello storico e risultati/fonti dell'analisi. Form, dialog e bozze rimangono locali. La versione usata per salvare una bozza dello storico resta quella sulla quale l'utente ha iniziato a modificare, anche se la cache viene aggiornata nel frattempo.

Non è stata aggiunta persistenza della cache su disco. Le operazioni Tauri funzionano offline tramite `networkMode: "always"`; le scritture non vengono ritentate automaticamente. Le chiavi includono ricerca, cursori e identificatori delle route. Le fonti sensibili vengono ricontrollate a ogni apertura e non conservate in cache dopo la chiusura.

Le invalidazioni dopo eventi/salvataggi cancellano esplicitamente le letture precedenti prima di richiederne di nuove. Questo evita che una prima risposta ancora pendente nasconda un evento più recente. La cancellazione scarta il risultato lato client, senza presumere che Tauri interrompa il comando nativo.

## Evidenza dei benefici

Il test `scripts/verify-state-renders.ts` compila una variante con contatori inseriti nei corpi dei componenti reali. L'instrumentazione esiste solo nella build temporanea del test. Emette 30 snapshot validi con livello audio e durata audio variabili, mantenendo il testo invariato, e attende un frame tra gli eventi.

| Componente                                   |          Render prima | Render dopo |
| -------------------------------------------- | --------------------: | ----------: |
| Sidebar                                      |                    30 |           0 |
| Pagina dettatura                             |                    30 |           0 |
| Pannello trascrizione                        |                    30 |           0 |
| Controlli registrazione                      |                    30 |           0 |
| Stato motore                                 |                    30 |           0 |
| Cronologia                                   |                    30 |           0 |
| Pagina impostazioni, in una seconda sequenza |                    30 |           0 |
| Misuratore audio isolato                     | incluso nei controlli |          30 |

Il test verifica anche l'aggiornamento del testo, il rifiuto degli snapshot con revisione precedente e una sola connessione nativa attraversando le route. I dati prima/dopo sono in `artifacts/state/renders-before.json` e `renders-after.json`. La misura riguarda il lavoro React evitato; non misura latenza ASR, CPU o consumo energetico del Mac.

Il test `scripts/verify-query-cache-ui.ts` verifica il ritorno al dizionario entro la finestra di freschezza: una sola lettura nativa, mentre l'hook precedente eseguiva una lettura a ogni mount. Verifica inoltre deduplicazione durante richieste pendenti, operazioni offline, paginazione, risposte di ricerca fuori ordine, eventi quando la route è chiusa ed eventi durante la prima lettura pendente. Quest'ultimo test è stato osservato fallire prima della correzione della cancellazione.

Il costo del nuovo stato client è circa 40 kB di JavaScript minificato, circa 12 kB gzip, sulla build locale rispetto alla baseline. Il beneficio misurato è la riduzione dei render e delle letture native ripetute.

## Riproduzione

- `pnpm desktop:check`: controllo TypeScript.
- `pnpm test:ui`: build e suite UI, inclusi cache e contatori dei render.
- `pnpm test:state`: solo build strumentata e misure dello stato.
- `node scripts/verify-query-cache-ui.ts`: test cache sugli asset già compilati.

I test browser intercettano gli asset e simulano il protocollo IPC Tauri, senza listener HTTP. Coprono i flussi frontend e i contratti delle chiamate; non registrano audio reale e non misurano il motore ASR. Le suite esistenti coprono anche bozze, errori/retry, privacy, consensi, suggerimenti, pianificazione e navigazione.

La baseline includeva modifiche dell'utente già presenti. Queste sono state conservate; il confronto per la review ha usato una copia dei file iniziali, senza creare worktree.
