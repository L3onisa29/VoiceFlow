// Production UI verification with Tauri IPC fixtures. No HTTP listener or audio.
// Uses pinned Playwright; PLAYWRIGHT_MODULE optionally selects another installation.
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const modulePath = process.env.PLAYWRIGHT_MODULE;
const { chromium } = await import(
  modulePath ? pathToFileURL(modulePath).href : "playwright"
);
const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "artifacts/history-ux-batch4");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1060, height: 760 },
  reducedMotion: "reduce",
});
await context.route("https://voiceflow.test/**", async (route) => {
  const pathname = new URL(route.request().url()).pathname;
  const file = pathname === "/" ? "index.html" : pathname.slice(1);
  const contentType = file.endsWith(".js")
    ? "text/javascript"
    : file.endsWith(".css")
      ? "text/css"
      : file.endsWith(".woff2")
        ? "font/woff2"
        : "text/html";
  await route.fulfill({
    body: await readFile(resolve(root, "apps/desktop/dist", file)),
    contentType,
  });
});
const mocks = (
  await readFile(
    resolve(root, "apps/desktop/node_modules/@tauri-apps/api/mocks.js"),
    "utf8",
  )
).replace(/export\s*\{[^}]*\};?/g, "");
await context.addInitScript({
  content:
    mocks +
    `
window.calls = [];
window.failEdit = false;
window.failCommand = null;
window.excluded = false;
window.state = {revision:1,phase:'recording',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:3,device:'Fixture',settings:{language:'italian',format:'spoken-commands',cleanup:true},transcript:{confirmed:'sessione attiva',partial:'',audioSeconds:3,inferenceSeconds:0,chunks:1,finished:false}};
const entry = {id:2,createdAt:1750000000000,text:'voce flo non 42',wordCount:4,durationSeconds:12,language:'it-IT',uncertainWords:1,unknownRemoved:1};
const metadata = {modelId:'nvidia/nemotron-3.5-asr-streaming-0.6b',modelRevision:'fixture-revision',modelProvenance:'fixture',decoderVersion:'fixture',processorVersion:'voiceflow-text-v1',format:'spoken-commands',cleanup:true,groups:[],dictionaryVersion:null,cleanupVersion:'builtin-v1'};
const details = {
  2:{entry,originalAsr:['<unk> voce flo non 42'],producedText:entry.text,processing:{...metadata,recognition:{meanConfidence:0.93,scoredTokens:12,threshold:0.5,lowWords:[{utterance:0,word:2}],lowWordsTotal:1,unknownRemoved:1,languageTags:{'<it-IT>':3,'<en-US>':1}},ruleHits:{'dictionary:-4':2},timing:{keyToMicrophoneMs:84,stopToPasteMs:612,pasted:true,inferenceMs:3100,chunks:27,silenceClosures:1,preparedDecoder:true}},editVersion:0,currentRevision:null,revisions:[]},
  1:{entry:{...entry,id:1,text:'Testo precedente',uncertainWords:0,unknownRemoved:0},originalAsr:null,producedText:'Testo precedente',processing:null,editVersion:0,currentRevision:null,revisions:[]}
};
window.details = details;
mockIPC(async (cmd,args) => {
  window.calls.push({cmd,args});
  if(cmd === window.failCommand) throw new Error('Errore fixture '+cmd);
  if(cmd === 'analysis_excluded') return window.excluded;
  if(cmd === 'analysis_exclude') {window.excluded=args.excluded; return;}
  if(cmd === 'get_state') return structuredClone(window.state);
  if(cmd === 'get_permissions') return {microphone:true,microphoneStatus:'authorized',accessibility:true};
  if(cmd === 'dictionary_auto_variants') return [];
  if(cmd === 'dictionary_snapshot') return {terms:[],groups:[],selectedGroupIds:[],version:1,conflicts:[]};
  if(cmd === 'history_page') {
    const all = Object.values(details).map(d=>structuredClone(d.entry)).sort((a,b)=>b.id-a.id);
    const matches = all.filter(e=>(args.query.beforeId===null || e.id<args.query.beforeId) && e.text.toLowerCase().includes(args.query.search.toLowerCase()));
    const entries = matches.slice(0,20);
    return {entries,nextCursor:matches.length>20?entries.at(-1).id:null,stats:{totalWords:all.reduce((sum,e)=>sum+e.wordCount,0),totalSessions:all.length,totalSeconds:all.reduce((sum,e)=>sum+e.durationSeconds,0),wordsPerMinute:20}};
  }
  if(cmd === 'history_detail') return structuredClone(details[args.id]);
  if(cmd === 'history_copy') return;
  if(cmd === 'history_delete') {delete details[args.id]; return;}
  const r = args.request;
  const d = r && details[r.id];
  if(cmd === 'history_edit') {
    if(window.delayEdit) await new Promise(resolve => window.releaseEdit=resolve);
    if(window.failEdit) throw new Error('Scrittura non riuscita');
    const old = d.revisions.find(v=>v.key===r.requestKey);
    if(!old) {
      if(r.expectedVersion!==d.editVersion) throw new Error('Versione cambiata');
      const revision = {id:d.revisions.length+1,createdAt:1750000010000,beforeText:d.entry.text,text:r.text,reverted:false,category:null,key:r.requestKey,parent:d.currentRevision};
      d.revisions.unshift(revision);d.entry.text=r.text;d.editVersion++;d.currentRevision=revision.id;
    }
    return structuredClone(d);
  }
  if(cmd === 'history_remember') {d.revisions.find(v=>v.id===r.revisionId).category=r.category;return structuredClone(d);}
  if(cmd === 'history_undo') {const revision=d.revisions.find(v=>v.id===d.currentRevision);d.entry.text=revision.beforeText;revision.reverted=true;revision.category=null;d.currentRevision=revision.parent;d.editVersion++;return structuredClone(d);}
  throw new Error('Unexpected IPC '+cmd);
}, {shouldMockEvents:true});
`,
});
const page = await context.newPage();
const errors: string[] = [];
page.on("pageerror", (error) => errors.push(String(error)));
page.on("console", (message) => {
  if (message.type() === "error") errors.push(message.text());
});
const checks: string[] = [];
const pass = (name: string) => {
  checks.push(name);
  console.log("PASS", name);
};
try {
  // Sonner region: transient success and action errors are toasts.
  const toasts = page.getByRole("region", { name: /^Notifiche/ });
  const historyCalls = (cmd: string) =>
    page.evaluate(
      `window.calls.filter(c => c.cmd === ${JSON.stringify(cmd)}).map(c => c.args)`,
    ) as Promise<any[]>;
  await page.goto("https://voiceflow.test/");
  await page
    .getByRole("heading", { level: 1, name: "Dettatura", exact: true })
    .waitFor();
  const session = page.getByRole("region", {
    name: "Dettatura in tempo reale",
  });
  const stats = page.getByRole("region", { name: "Statistiche", exact: true });
  const recent = page.getByRole("region", { name: "Recenti", exact: true });
  await stats.getByText("2", { exact: true }).first().waitFor();
  const top = async (locator: typeof stats) => (await locator.boundingBox())!.y;
  // Session card first, then the secondary stats, then the recents.
  assert.ok((await top(session)) < (await top(stats)));
  assert.ok((await top(stats)) < (await top(recent)));
  await recent.locator("li").nth(1).waitFor();
  assert.equal(await recent.locator("li").count(), 2);
  await recent
    .getByRole("button", { name: /Copia trascrizione/ })
    .first()
    .click();
  await toasts.getByText("Testo copiato.", { exact: true }).waitFor();
  assert.deepEqual(
    (await historyCalls("history_copy")).map((args) => args.id),
    [2],
  );
  // The full list lives in Storico; the home only shows the recents.
  const list = page.getByRole("region", { name: "Trascrizioni", exact: true });
  assert.equal(await list.count(), 0);
  await recent.getByRole("link", { name: "Vedi tutto lo storico" }).click();
  await page.waitForURL("https://voiceflow.test/#/history");
  await page
    .getByRole("heading", { level: 1, name: "Storico", exact: true })
    .waitFor();
  assert.equal(
    await page.getByText("Cerca nelle trascrizioni", { exact: true }).count(),
    0,
  );
  const searchbox = page.getByRole("searchbox", {
    name: "Cerca nelle trascrizioni",
  });
  assert.equal(await searchbox.count(), 1);
  await page.keyboard.press("Meta+f");
  assert.equal(
    await searchbox.evaluate((element) => element === document.activeElement),
    true,
  );
  await list.locator("li").nth(1).waitFor();
  assert.equal(await list.locator("li").count(), 2);
  // Both fixtures were dictated on the same day: one day heading.
  const days = list.getByRole("heading", { level: 2 });
  assert.equal(await days.count(), 1);
  assert.match(await days.innerText(), /^(Oggi|Ieri|\S+ \d+ \S+ \d{4})$/);
  const row = list.locator("li").first();
  await row.getByText("1 parola non riconosciuta e omessa").waitFor();
  await row.getByText("1 parola incerta", { exact: true }).waitFor();
  await row.getByText("4 parole", { exact: true }).waitFor();
  await row.getByText("12 s", { exact: true }).waitFor();
  assert.equal(
    await list
      .locator("li")
      .nth(1)
      .getByText(/incert|omess/)
      .count(),
    0,
  );
  // The whole row opens the detail; secondary actions sit in an icon menu.
  assert.equal(await row.getByRole("link").count(), 1);
  const rowMenu = row.getByRole("button", {
    name: /Azioni per la trascrizione/,
  });
  assert.equal((await rowMenu.innerText()).trim(), "");
  await rowMenu.click();
  assert.deepEqual(
    (await page.getByRole("menuitem").allInnerTexts()).map((text) =>
      text.trim(),
    ),
    ["Copia", "Apri", "Elimina"],
  );
  await page.getByRole("menuitem", { name: "Copia", exact: true }).click();
  await page.waitForFunction(
    "window.calls.filter(c => c.cmd === 'history_copy').length === 2",
  );
  pass(
    "Home con sessione, statistiche e recenti; Storico con ricerca ⌘F, righe per giorno e menu Copia/Apri/Elimina",
  );
  await page.screenshot({ path: resolve(output, "history-top.png") });

  await row.getByRole("link").click();
  await page
    .getByRole("heading", { level: 1, name: "Trascrizione", exact: true })
    .waitFor();
  assert.ok(page.url().includes("#/history/2"));
  const editor = page.locator('section[aria-labelledby="transcription-title"]');
  const text = editor.getByLabel("Testo corretto", { exact: true });
  await text.waitFor();
  assert.equal(await text.inputValue(), "voce flo non 42");
  // One editable text, no read-only duplicate and no extra scroll areas.
  assert.equal(await editor.getByRole("textbox").count(), 1);
  assert.equal(await editor.getByRole("article").count(), 0);
  assert.equal(await editor.locator('[data-slot="scroll-area"]').count(), 0);
  assert.equal(await editor.getByRole("combobox").count(), 0);
  await editor.getByLabel("Avvisi del riconoscimento").waitFor();
  const tab = (name: string) => editor.getByRole("tab", { name });
  assert.equal(await tab("Confronto").getAttribute("aria-selected"), "true");
  await editor.getByText("<unk> voce flo non 42", { exact: true }).waitFor();
  assert.deepEqual(await editor.locator("mark").allInnerTexts(), ["flo"]);
  await editor
    .getByText("Nessuna correzione salvata.", { exact: true })
    .waitFor();
  await tab("Dettagli").click();
  await editor
    .getByRole("heading", { name: "Sessione", exact: true })
    .waitFor();
  await editor
    .getByRole("checkbox", {
      name: "Escludi questa sessione dall'analisi con OpenAI",
    })
    .waitFor();
  await editor.getByText("93% su 12 token").waitFor();
  await editor.getByText("it-IT × 3, en-US × 1").waitFor();
  await editor.getByText("84 ms · decoder già pronto").waitFor();
  await editor.getByText("Dallo stop all'incolla").waitFor();
  await editor.getByText("3100 ms in 27 chunk").waitFor();
  // Inactive tabs are not rendered: the original is only in Confronto.
  assert.equal(
    await editor.getByText("<unk> voce flo non 42", { exact: true }).count(),
    0,
  );
  assert.equal(await text.inputValue(), "voce flo non 42");
  pass(
    "Testo corretto modificabile in primo piano; originale in Confronto e dati di sessione in Dettagli",
  );
  await page.screenshot({ path: resolve(output, "history-detail.png") });

  await text.fill("VoiceFlow non 42");
  await editor.getByText("Modifiche non salvate", { exact: true }).waitFor();
  await editor.getByRole("link", { name: "Torna allo storico" }).click();
  const discard = page.getByRole("alertdialog");
  await discard.getByRole("button", { name: "Continua a modificare" }).click();
  assert.equal(await text.inputValue(), "VoiceFlow non 42");
  await page.evaluate("window.failEdit = true");
  await editor.getByRole("button", { name: "Salva correzione" }).click();
  await toasts.getByText("Scrittura non riuscita", { exact: true }).waitFor();
  assert.equal(await text.inputValue(), "VoiceFlow non 42");
  await page.evaluate("window.failEdit = false");
  await editor.getByRole("button", { name: "Salva correzione" }).click();
  await toasts.getByText("Correzione salvata.", { exact: true }).waitFor();
  await editor
    .getByText("Nessuna modifica in sospeso", { exact: true })
    .waitFor();
  const keys = (await historyCalls("history_edit")).map(
    (args) => args.request.requestKey,
  );
  assert.equal(keys[0], keys[1]);
  await tab("Confronto").click();
  await editor.locator("ins").first().waitFor();
  assert.ok((await editor.locator("del").count()) > 0);
  await tab("Revisioni").click();
  await editor.getByRole("radio", { name: "Riconoscimento" }).click();
  await editor
    .getByRole("button", { name: "Ricorda questa correzione" })
    .click();
  await toasts.getByText("Correzione ricordata.", { exact: true }).waitFor();
  await editor
    .getByRole("heading", { name: "Ricorda per l’analisi · Riconoscimento" })
    .waitFor();
  await editor.getByRole("button", { name: "Copia testo salvato" }).click();
  await page.waitForFunction(
    "window.calls.filter(c => c.cmd === 'history_copy').length === 3",
  );
  assert.equal((await historyCalls("history_copy")).at(-1).id, 2);
  await editor
    .getByRole("button", { name: "Annulla ultima correzione" })
    .click();
  await toasts
    .getByText("Ultima correzione annullata.", { exact: true })
    .waitFor();
  assert.equal(await text.inputValue(), "voce flo non 42");
  // Save and remember in one step, from the split button.
  await text.fill("voce flow non 42");
  await editor
    .getByRole("button", { name: "Salva e ricorda per l’analisi" })
    .click();
  await page.getByRole("menuitem", { name: "Stile", exact: true }).click();
  await toasts
    .getByText("Correzione salvata e ricordata.", { exact: true })
    .waitFor();
  const remembered = (await historyCalls("history_remember")).at(-1).request;
  assert.equal(remembered.category, "style");
  assert.equal(
    remembered.revisionId,
    await page.evaluate("window.details[2].currentRevision"),
  );
  assert.equal(await text.inputValue(), "voce flow non 42");
  await editor
    .getByRole("heading", { name: "Ricorda per l’analisi · Stile" })
    .waitFor();
  // Annulla modifiche discards only the draft.
  await text.fill("bozza da scartare");
  await editor
    .getByRole("button", { name: "Annulla modifiche", exact: true })
    .click();
  assert.equal(await text.inputValue(), "voce flow non 42");
  pass(
    "La bozza blocca la navigazione; salvataggio idempotente, ricorda, salva e ricorda, copia, annullamento e scarto restano disponibili",
  );

  await editor.getByRole("link", { name: "Torna allo storico" }).click();
  await list.locator("li").first().waitFor();
  const longText =
    "Vorrei organizzare meglio il lavoro della prossima settimana, partendo dalle attività che richiedono maggiore concentrazione. La prima parte della mattina sarà dedicata alla revisione del progetto e alla preparazione delle modifiche da discutere con il gruppo. Prima di iniziare, raccoglierò le osservazioni arrivate durante le ultime prove e le confronterò con gli obiettivi concordati. Questo dovrebbe aiutarci a distinguere le correzioni urgenti dalle idee che possiamo valutare in seguito.\n\nNel pomeriggio passerò alle verifiche pratiche. Voglio controllare che i passaggi più frequenti siano chiari e che le informazioni utili siano facili da trovare. Quando una schermata mostra lo stesso contenuto più volte, la lettura diventa faticosa e le azioni principali perdono evidenza. Per questo preferisco una pagina semplice, con il testo al centro e pochi strumenti ben riconoscibili. I dettagli tecnici possono rimanere disponibili senza occupare subito tutto lo spazio.\n\nAlla fine della giornata raccoglierò i risultati in una breve nota. Segnerò ciò che funziona, i problemi ancora aperti e il prossimo passo concreto. Questa nota sarà il punto di partenza della riunione successiva e ci permetterà di riprendere il lavoro senza ricostruire ogni volta tutte le decisioni.";
  await page.evaluate((text) => {
    const w = window as any;
    w.details[1].entry.text = text;
    w.details[1].producedText = text;
    w.details[1].originalAsr = [text];
  }, longText);
  await list.getByRole("link").last().click();
  await text.waitFor();
  assert.equal(await text.inputValue(), longText);
  assert.equal(await editor.getByRole("combobox").count(), 0);
  assert.equal(await editor.getByRole("textbox").count(), 1);
  await page.screenshot({ path: resolve(output, "history-detail-long.png") });
  await page.setViewportSize({ width: 780, height: 620 });
  await page.screenshot({ path: resolve(output, "history-detail-small.png") });
  await page.locator("#page-content").evaluate((element) => {
    element.scrollTop = 300;
  });
  // The action bar stays in view while the page scrolls through a long text.
  const saveAction = await editor
    .getByRole("button", { name: "Salva correzione" })
    .boundingBox();
  assert.ok(
    saveAction && saveAction.y >= 38 && saveAction.y + saveAction.height <= 620,
  );
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  assert.equal(
    await text.evaluate(
      (element) => element.scrollHeight <= element.clientHeight + 2,
    ),
    true,
  );
  await editor.getByRole("link", { name: "Torna allo storico" }).click();
  await page.setViewportSize({ width: 1060, height: 760 });
  pass(
    "Testo lungo a scorrimento di pagina, barra azioni sempre visibile e un solo editor che cresce con il contenuto",
  );
  await page.evaluate(() => {
    const w = window as any;
    for (let id = 3; id <= 25; id++) {
      const entry = {
        id,
        createdAt: 1750000000000 + id * 1000,
        text: `Trascrizione numero ${id}`,
        wordCount: 3,
        durationSeconds: 10,
        language: "it-IT",
      };
      w.details[id] = {
        entry,
        originalAsr: null,
        producedText: entry.text,
        processing: null,
        editVersion: 0,
        currentRevision: null,
        revisions: [],
      };
    }
  });
  // No manual refresh: the native history event updates the list.
  await page.evaluate(
    "window.__TAURI_INTERNALS__.invoke('plugin:event|emit', {event: 'history-changed', payload: null})",
  );
  await list
    .locator("li")
    .first()
    .getByText("Trascrizione numero 25", { exact: true })
    .waitFor();
  assert.equal(await list.getByRole("button", { name: /Aggiorna/ }).count(), 0);
  assert.equal(await list.locator("li").count(), 20);
  await list.getByRole("button", { name: "Pagina successiva" }).click();
  await list.getByText("Pagina 2").waitFor();
  assert.equal(await list.locator("li").count(), 5);
  await list.getByRole("button", { name: "Pagina precedente" }).click();
  await list.getByText("Pagina 1").waitFor();
  assert.equal(await list.locator("li").count(), 20);
  pass("Lo storico mostra 20 righe per pagina e naviga avanti e indietro");

  await searchbox.fill("numero 25");
  await list
    .locator("li")
    .filter({ hasText: "Trascrizione numero 25" })
    .waitFor();
  assert.equal(await list.locator("li").count(), 1);
  await searchbox.fill("nessuna corrispondenza");
  await list.getByText("Nessun risultato", { exact: true }).waitFor();
  assert.equal(await list.locator("li").count(), 0);
  await list.getByRole("button", { name: "Cancella ricerca" }).click();
  assert.equal(await searchbox.inputValue(), "");
  await list.locator("li").first().waitFor();
  const firstMenu = list
    .locator("li")
    .first()
    .getByRole("button", { name: /Azioni per la trascrizione/ });
  await firstMenu.click();
  await page.getByRole("menuitem", { name: "Elimina", exact: true }).click();
  await discard.getByRole("button", { name: "Annulla" }).click();
  await discard.waitFor({ state: "hidden" });
  assert.equal(await page.evaluate("Object.keys(window.details).length"), 25);
  // Focus goes back to the menu that opened the confirmation (Radix moves it
  // on the tick after the dialog closes).
  await page
    .waitForFunction(() =>
      document.activeElement?.hasAttribute("data-history-actions"),
    )
    .catch(() => {});
  assert.match(
    (await page.evaluate(() =>
      document.activeElement?.getAttribute("aria-label"),
    )) ?? "",
    /^Azioni per la trascrizione/,
  );
  await firstMenu.click();
  await page.getByRole("menuitem", { name: "Elimina", exact: true }).click();
  await discard.getByRole("button", { name: "Elimina" }).click();
  await discard.waitFor({ state: "hidden" });
  await page.waitForFunction("Object.keys(window.details).length === 24");
  await toasts.getByText("Trascrizione eliminata.", { exact: true }).waitFor();
  await page.screenshot({ path: resolve(output, "history-list-780.png") });
  // Statistics stay on the home page and reflect the deletion.
  await page.getByRole("link", { name: "Dettatura", exact: true }).click();
  await stats.getByText("24", { exact: true }).first().waitFor();
  pass(
    "Ricerca, ricerca vuota e cancellazione aggiornano la lista e le statistiche",
  );

  assert.deepEqual(errors, []);
  pass("Nessun errore nel browser");
} catch (error) {
  await page.screenshot({ path: resolve(output, "failure.png") });
  console.log(await page.locator("body").innerText());
  throw error;
} finally {
  await browser.close();
}
await writeFile(
  resolve(output, "result.json"),
  JSON.stringify(
    {
      checks,
      screenshots: [
        "history-top.png",
        "history-detail.png",
        "history-detail-long.png",
        "history-detail-small.png",
        "history-list-780.png",
      ],
      backend: "Tauri IPC fixtures; SQLite tested separately in Rust",
    },
    null,
    2,
  ),
);
