# Modello e ragionamento dell'analisi

Verifica del 26 settembre 2026, Codex CLI 0.157.1.

Le impostazioni ora salvano modello e ragionamento, con default `gpt-6-luna` e `medium`. I database precedenti ricevono questi valori in lettura senza modificare consenso o limiti. Ogni tentativo usa la configurazione letta nella transazione di prenotazione del budget, passando modello e ragionamento espliciti alla CLI.

## Verifiche locali

- `cargo test --workspace --locked`: 200 test passati, 5 test manuali ignorati.
- `cargo build --workspace --locked`: build nativa completata.
- `pnpm desktop:ui`: TypeScript e build frontend completati.
- `scripts/verify-analysis-ui.ts`: controlli su asset di produzione e IPC simulato. Verificati default, modello non valido, salvataggio ad analisi disattivata, cambio del ragionamento, invio delle preferenze e rilettura dello stato.
- Test Rust su impostazioni precedenti, persistenza SQLite, valori non validi, invalidazione dei lotti e argomenti ricevuti da un processo CLI sostitutivo.

Screenshot dei nuovi controlli: `artifacts/analysis-d1/model-reasoning.png`.

## Confronto remoto

Il test manuale usa lo stesso provider, prompt, schema e ingestione dell'app. Crea database temporanei con sei frasi sintetiche e tre correzioni ricordate. Non legge le sessioni dell'utente e mantiene disattivata l'approvazione automatica.

Tre casi correggibili: grafia di VoiceFlow, articolo ripetuto e frase di due parole ripetuta. Tre controlli da preservare: enfasi intenzionale, negazione ambigua e numeri.

| Ragionamento | Durata | Rilievi ricevuti | Rilievi validati | Rilievi respinti | Controlli con modifiche proposte |
| --- | ---: | ---: | ---: | ---: | ---: |
| Basso | 30,35 s | 3 | 0 | 3 | 0/3 |
| Medio | 34,41 s | 6 | 4 | 2 | 0/3 |

Entrambi hanno individuato i tre casi correggibili. Basso ha restituito estratti diversi dall'input dei propri esempi positivi, violando il contratto già indicato nel prompt; la validazione li ha respinti. Medio ha prodotto anche tre rilievi `keep`. I conteggi validati includono i rilievi di conservazione e non equivalgono al numero di regole correttive. Entrambi i lotti sono terminati come `partial`, perché contenevano rilievi respinti.

Manteniamo medio come default. Questo singolo confronto per livello suggerisce una migliore aderenza al contratto, ma non misura l'accuratezza su dettature reali, non dimostra superiorità generale e non confronta costi o token. Le risposte sintetiche complete sono in `artifacts/analysis-model-reasoning/remote-results.json`.

Per ripetere il confronto, con autenticazione Codex già disponibile:

```sh
cargo test -p voiceflow-desktop --lib compare_luna_reasoning_on_synthetic_phrases --locked -- --ignored --nocapture
```

Il comando effettua due richieste remote, ciascuna con timeout di 120 secondi. È ignorato dai test ordinari.

## Compatibilità verificata durante il confronto

La verifica remota ha rilevato tre incompatibilità preesistenti, corrette senza abilitare strumenti:

- `tools.view_image=false` non è più accettato da `--strict-config`. Resta `--disable view_image`, supportato dalla CLI.
- OpenAI richiede il tipo esplicito anche accanto ai campi stringa `enum` e `const` dello schema.
- La CLI emette un avviso locale quando il code-mode host è disattivato. Il parser accetta soltanto quel testo esatto prima di `turn.started`. Rifiuta avvisi sconosciuti, lo stesso avviso durante il turno ed eventi relativi a strumenti. Gli avvisi generici sulle feature sperimentali sono soppressi.

Le richieste finali sono passate attraverso il provider e la validazione reali dopo queste correzioni.
