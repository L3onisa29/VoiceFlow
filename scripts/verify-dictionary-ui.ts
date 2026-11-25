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
const output = resolve(root, "artifacts/dictionary-ux-batch1");
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
window.calls=[];window.failSave=false;window.failIds=[];
const empty={variants:[],language:'*',matching:'exact',context:{before:null,after:null,exceptBefore:[],exceptAfter:[]}};
window.dictionary={terms:[{id:1,text:'VoiceFlow',groupIds:[1],...empty,variants:['voice flow']},{id:2,text:'Promemoria',groupIds:[],...empty}],groups:[{id:1,name:'Progetto'},{id:2,name:'Lavoro'}],selectedGroupIds:[1],version:3,conflicts:[]};
mockIPC(async(cmd,args)=>{
  window.calls.push({cmd,args});
  if(cmd==='get_state')return {revision:1,phase:'recording',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:3,device:'Fixture',settings:{language:'italian',format:'spoken-commands',cleanup:true},transcript:{confirmed:'sessione attiva',partial:'',audioSeconds:3,inferenceSeconds:0,chunks:1,finished:false}};
  if(cmd==='get_permissions')return {microphone:true,microphoneStatus:'authorized',accessibility:true};
  if(cmd==='history_page')return {entries:[],nextCursor:null,stats:{totalWords:0,totalSessions:0,totalSeconds:0,wordsPerMinute:0}};
  if(cmd==='dictionary_snapshot')return structuredClone(window.dictionary);
  if(cmd==='dictionary_auto_variants')return structuredClone(window.auto??[]);
  if(cmd==='dictionary_remove_auto_variant'){
    window.auto=(window.auto??[]).filter(v=>v.spelling!==args.spelling);
    for(const term of window.dictionary.terms)term.variants=term.variants.filter(v=>v!==args.spelling);
    window.dictionary.version++;
    return structuredClone(window.auto);
  }
  if(cmd==='dictionary_select_groups'){window.dictionary.selectedGroupIds=args.ids;window.dictionary.version++;return structuredClone(window.dictionary);}
  if(cmd==='dictionary_save_term'){
    if(window.delaySave) await new Promise(resolve => window.releaseSave=resolve);
    if(window.failSave||window.failIds.includes(args.input.id))throw new Error('Scrittura non riuscita');
    const input=args.input;
    const term={...input,id:input.id??Math.max(0,...window.dictionary.terms.map(t=>t.id))+1};
    window.dictionary.terms=window.dictionary.terms.filter(t=>t.id!==term.id).concat([term]);window.dictionary.version++;
    return structuredClone(window.dictionary);
  }
  if(cmd==='dictionary_save_group'){
    if(window.failSave)throw new Error('Scrittura non riuscita');
    const group={...args.input,id:args.input.id??Math.max(0,...window.dictionary.groups.map(g=>g.id))+1};
    window.dictionary.groups=window.dictionary.groups.filter(g=>g.id!==group.id).concat([group]);window.dictionary.version++;return structuredClone(window.dictionary);
  }
  if(cmd==='dictionary_delete_group'){
    if(window.failSave)throw new Error('Scrittura non riuscita');
    window.dictionary.groups=window.dictionary.groups.filter(g=>g.id!==args.id);
    window.dictionary.selectedGroupIds=window.dictionary.selectedGroupIds.filter(id=>id!==args.id);
    window.dictionary.terms=window.dictionary.terms.map(t=>({...t,groupIds:t.groupIds.filter(id=>id!==args.id)}));window.dictionary.version++;return structuredClone(window.dictionary);
  }
  if(cmd==='dictionary_delete_term'){
    if(window.failSave||window.failIds.includes(args.id))throw new Error('Scrittura non riuscita');
    window.dictionary.terms=window.dictionary.terms.filter(t=>t.id!==args.id);window.dictionary.version++;return structuredClone(window.dictionary);
  }
  throw new Error('Unexpected IPC '+cmd);
},{shouldMockEvents:true});`,
});
const page = await context.newPage();
const errors: string[] = [];
page.on("pageerror", (error) => errors.push(String(error)));
page.on("console", (message) => {
  if (message.type() === "error") errors.push(message.text());
});
const checks: string[] = [];
// Rows open the editor; the row actions menu holds Modifica, Duplica, Elimina.
const row = (text: string) =>
  page
    .locator("tbody tr")
    .filter({ has: page.getByText(text, { exact: true }) });
const openTerm = (text: string) =>
  row(text).getByText(text, { exact: true }).click();
const rowAction = async (text: string, action: string) => {
  await row(text)
    .getByRole("button", { name: `Azioni per ${text}`, exact: true })
    .click();
  await page.getByRole("menuitem", { name: action, exact: true }).click();
};
// The manual refresh button is gone: a window focus refetches local queries.
const refresh = () =>
  page.evaluate(() => window.dispatchEvent(new Event("focus")));
const groupsButton = () =>
  page.getByRole("button", { name: "Gruppi", exact: true });
const count = (cmd: string) =>
  page.evaluate(
    (name) =>
      (window as any).calls.filter((c: { cmd: string }) => c.cmd === name)
        .length,
    cmd,
  );
const pass = (name: string) => {
  checks.push(name);
  console.log("PASS", name);
};
try {
  await page.goto("https://voiceflow.test/#/dictionary");
  await page
    .getByRole("heading", { name: "Dizionario", exact: true })
    .waitFor();
  await page
    .getByText("Promemoria · Nessuna variante", { exact: true })
    .waitFor();
  const activeGroups = page.getByRole("button", { name: /^Gruppi attivi/ });
  const activeCount = activeGroups.locator('[data-slot="badge"]');
  assert.equal(await activeCount.textContent(), "1");
  await activeGroups.click();
  await page.getByText("Per la prossima dettatura", { exact: true }).waitFor();
  assert.equal(
    await page.locator("#active-group-1").getAttribute("aria-checked"),
    "true",
  );
  const group = page.locator("#active-group-2");
  await group.click();
  await page.waitForFunction(
    () =>
      document
        .querySelector("#active-group-2")
        ?.getAttribute("aria-checked") === "true",
  );
  assert.deepEqual(
    await page.evaluate("window.dictionary.selectedGroupIds"),
    [1, 2],
  );
  await page.keyboard.press("Escape");
  await group.waitFor({ state: "hidden" });
  assert.equal(await activeCount.textContent(), "2");
  pass(
    "Manual groups persist through IPC while the active session remains visible",
  );
  await openTerm("VoiceFlow");
  const dialog = page.getByRole("dialog");
  assert.equal(
    await dialog.getByLabel("Varianti confermate, una per riga").inputValue(),
    "voice flow",
  );
  await dialog
    .getByLabel("Varianti confermate, una per riga")
    .fill("one two three four five six seven eight");
  assert.equal(
    await dialog
      .getByRole("button", { name: "Conferma varianti e salva" })
      .isDisabled(),
    true,
  );
  await dialog.getByText(/Non applicabile: massimo 7 parole/).waitFor();
  pass(
    "Eight-word variants are visibly non-applicable and cannot be submitted",
  );
  await dialog
    .getByLabel("Varianti confermate, una per riga")
    .fill("voice flow\nvoce flo");
  await dialog
    .getByRole("button", { name: "Lingua e condizioni avanzate" })
    .click();
  await dialog.locator("#term-language").click();
  await page.getByRole("option", { name: "Italiano", exact: true }).click();
  await dialog.locator("#term-matching").click();
  await page
    .getByRole("option", { name: "Ignora maiuscole Unicode", exact: true })
    .click();
  await dialog.getByLabel("Solo dopo", { exact: true }).fill("progetto");
  await dialog.getByLabel("Solo prima di", { exact: true }).fill("funziona");
  await dialog.getByLabel("Escludi dopo, una per riga").fill("non");
  await dialog.getByLabel("Escludi prima di, una per riga").fill("42");
  await page.evaluate("window.failSave=true");
  await dialog
    .getByRole("button", { name: "Conferma varianti e salva" })
    .click();
  await dialog.getByText("Scrittura non riuscita", { exact: true }).waitFor();
  assert.equal(
    await dialog.getByLabel("Varianti confermate, una per riga").inputValue(),
    "voice flow\nvoce flo",
  );
  assert.equal(await dialog.locator('[tabindex="-1"]:focus').count(), 1);
  await page.evaluate("window.failSave=false");
  await page.screenshot({ path: resolve(output, "dictionary-edit-1060.png") });
  await page.evaluate("window.delaySave=true");
  await dialog
    .getByRole("button", { name: "Conferma varianti e salva" })
    .click();
  await page.waitForFunction("!!window.releaseSave");
  await page.keyboard.press("Escape");
  await page.mouse.click(30, 300);
  assert.equal(await page.getByRole("alertdialog").count(), 0);
  assert.equal(await dialog.isVisible(), true);
  assert.equal(
    await dialog.getByLabel("Varianti confermate, una per riga").inputValue(),
    "voice flow\nvoce flo",
  );
  await page.evaluate("window.delaySave=false; window.releaseSave()");
  pass("Dictionary save in flight resists Escape and backdrop dismissal");
  await dialog.waitFor({ state: "hidden" });
  const saved = await page.evaluate(
    "window.dictionary.terms.find(t=>t.id===1)",
  );
  assert.deepEqual(saved.variants, ["voice flow", "voce flo"]);
  assert.equal(saved.language, "it");
  assert.equal(saved.matching, "unicode-lowercase");
  assert.deepEqual(saved.context, {
    before: "progetto",
    after: "funziona",
    exceptBefore: ["non"],
    exceptAfter: ["42"],
  });
  pass(
    "Variant confirmation saves language, matching, contexts and exceptions; failed writes preserve input",
  );
  await page.getByText("Parola salvata", { exact: true }).waitFor();
  await page.evaluate("window.dictionary.conflicts=[1,2]");
  await refresh();
  await page.getByText(/Varianti in conflitto tra 2 termini/).waitFor();
  assert.equal(await page.getByText("Conflitto", { exact: true }).count(), 2);
  pass(
    "Unresolved conflicts are visible in management and marked on the affected terms",
  );
  await page.setViewportSize({ width: 780, height: 620 });
  await rowAction("VoiceFlow", "Modifica");
  await dialog.getByRole("heading", { name: "Modifica parola" }).waitFor();
  await dialog
    .getByRole("button", { name: "Conferma varianti e salva" })
    .scrollIntoViewIfNeeded();
  assert.equal(
    await dialog
      .getByRole("button", { name: "Conferma varianti e salva" })
      .isVisible(),
    true,
  );
  await page.screenshot({ path: resolve(output, "dictionary-edit-780.png") });
  await dialog.getByRole("button", { name: "Annulla", exact: true }).click();
  await page
    .getByRole("button", { name: "Aggiungi parola", exact: true })
    .click();
  await dialog
    .getByLabel("Grafia preferita", { exact: true })
    .fill("Solo nota");
  await dialog
    .getByRole("button", { name: "Salva parola", exact: true })
    .click();
  await dialog.waitFor({ state: "hidden" });
  await page.getByText("Parola aggiunta", { exact: true }).waitFor();
  assert.deepEqual(
    await page.evaluate(
      "window.dictionary.terms.find(t=>t.text==='Solo nota').variants",
    ),
    [],
  );
  pass(
    "Small-window editor stays usable; a term without variants stays a note",
  );

  // Dirty guards preserve both term and group drafts and recover keyboard focus.
  await openTerm("Solo nota");
  await dialog
    .getByLabel("Grafia preferita", { exact: true })
    .fill("Bozza da conservare");
  await page.keyboard.press("Escape");
  const discard = page.getByRole("alertdialog");
  await discard.getByRole("button", { name: "Continua a modificare" }).click();
  await discard.waitFor({ state: "hidden" });
  assert.equal(
    await dialog.getByLabel("Grafia preferita", { exact: true }).inputValue(),
    "Bozza da conservare",
  );
  await page.waitForFunction(() => document.activeElement?.id === "term-text");
  for (let attempt = 0; attempt < 3; attempt++) {
    await page.mouse.click(30, 300);
    await discard
      .getByRole("button", { name: "Continua a modificare" })
      .click();
    await discard.waitFor({ state: "hidden" });
    await page.waitForFunction(
      () => document.activeElement?.id === "term-text",
    );
    assert.equal(
      await dialog.getByLabel("Grafia preferita", { exact: true }).inputValue(),
      "Bozza da conservare",
    );
    await page.keyboard.press("Escape");
    await discard
      .getByRole("button", { name: "Continua a modificare" })
      .focus();
    await page.keyboard.press("Enter");
    await discard.waitFor({ state: "hidden" });
    await page.waitForFunction(
      () => document.activeElement?.id === "term-text",
    );
  }
  pass(
    "Backdrop and keyboard cancellation repeatedly restore the term editor and preserve its draft",
  );
  assert.equal(
    await dialog
      .getByLabel("Grafia preferita", { exact: true })
      .evaluate((el) => el === document.activeElement),
    true,
  );
  await dialog.getByRole("button", { name: "Annulla", exact: true }).click();
  await discard.getByRole("button", { name: "Scarta modifiche" }).click();
  await dialog.waitFor({ state: "hidden" });
  await page.locator("tbody tr:focus").waitFor();
  assert.equal(
    await row("Solo nota").evaluate((el) => el === document.activeElement),
    true,
  );
  pass(
    "Escape and cancel protect a dirty term; discard restores the opener focus",
  );

  await groupsButton().click();
  const manager = page.getByRole("dialog", {
    name: "Gestisci gruppi",
    exact: true,
  });
  await manager.getByRole("button", { name: "Crea gruppo" }).click();
  const groupDialog = page.getByRole("dialog", {
    name: "Crea gruppo",
    exact: true,
  });
  await groupDialog.getByLabel("Nome del gruppo").fill("Cliente");
  await groupDialog
    .getByRole("button", { name: "Chiudi", exact: true })
    .click();
  await discard.getByRole("button", { name: "Continua a modificare" }).click();
  await discard.waitFor({ state: "hidden" });
  assert.equal(
    await groupDialog.getByLabel("Nome del gruppo").inputValue(),
    "Cliente",
  );
  await groupDialog.locator('[data-slot="dialog-close"]:focus').waitFor();
  await groupDialog.getByLabel("Nome del gruppo").click();
  await page.mouse.click(30, 300);
  await discard.getByRole("button", { name: "Continua a modificare" }).click();
  await discard.waitFor({ state: "hidden" });
  await page.waitForFunction(() => document.activeElement?.id === "group-name");
  assert.equal(
    await groupDialog.getByLabel("Nome del gruppo").inputValue(),
    "Cliente",
  );
  pass(
    "Backdrop cancellation restores the group name without focusing the underlying group manager",
  );
  await page.evaluate("window.failSave=true");
  await groupDialog.getByRole("button", { name: "Salva gruppo" }).click();
  await groupDialog
    .getByText("Scrittura non riuscita", { exact: true })
    .waitFor();
  assert.equal(
    await groupDialog.getByLabel("Nome del gruppo").inputValue(),
    "Cliente",
  );
  await page.evaluate("window.failSave=false");
  await groupDialog.getByRole("button", { name: "Salva gruppo" }).click();
  await groupDialog.waitFor({ state: "hidden" });
  await manager
    .getByRole("button", { name: "Rinomina gruppo Cliente" })
    .click();
  const rename = page.getByRole("dialog", {
    name: "Rinomina gruppo",
    exact: true,
  });
  await rename.getByLabel("Nome del gruppo").fill("Cliente nuovo");
  await rename.getByRole("button", { name: "Salva gruppo" }).click();
  await rename.waitFor({ state: "hidden" });
  assert.deepEqual(
    await page.evaluate("window.dictionary.selectedGroupIds"),
    [1, 2],
  );
  await page.screenshot({ path: resolve(output, "dictionary-groups-780.png") });
  await manager.getByRole("button", { name: "Chiudi", exact: true }).click();
  await manager.waitFor({ state: "hidden" });
  // Radix restores focus on the tick after the sheet unmounts.
  await groupsButton().evaluate(
    (el) =>
      new Promise<void>((done, fail) => {
        const until = Date.now() + 2000;
        const poll = () =>
          el === document.activeElement
            ? done()
            : Date.now() > until
              ? fail(new Error("Gruppi did not regain focus"))
              : requestAnimationFrame(poll);
        poll();
      }),
  );
  pass(
    "Group create, failed save, rename and dirty close work independently of filters and activation",
  );

  // An exclusive member becomes global; a shared member keeps its other group.
  await openTerm("Solo nota");
  await dialog.getByLabel("Progetto", { exact: true }).click();
  await dialog.getByLabel("Lavoro", { exact: true }).click();
  await dialog
    .getByRole("button", { name: "Salva parola", exact: true })
    .click();
  await dialog.waitFor({ state: "hidden" });
  await page.locator("#dictionary-filter").click();
  await page.getByRole("option", { name: "Progetto (2)", exact: true }).click();
  await groupsButton().click();
  await manager
    .getByRole("button", { name: "Elimina gruppo Progetto" })
    .click();
  await discard
    .getByText(/2 parole restano nel dizionario. 1 diventa senza gruppo/)
    .waitFor();
  await page.screenshot({
    path: resolve(output, "dictionary-delete-group-780.png"),
  });
  await page.evaluate("window.failSave=true");
  await discard.getByRole("button", { name: "Elimina", exact: true }).click();
  await discard.getByText("Scrittura non riuscita", { exact: true }).waitFor();
  await page.evaluate("window.failSave=false");
  await discard.getByRole("button", { name: "Elimina", exact: true }).click();
  await discard.waitFor({ state: "hidden" });
  assert.deepEqual(
    await page.evaluate("window.dictionary.terms.find(t=>t.id===1).groupIds"),
    [],
  );
  assert.deepEqual(
    await page.evaluate(
      "window.dictionary.terms.find(t=>t.text==='Solo nota').groupIds",
    ),
    [2],
  );
  await manager.locator("button:focus").waitFor();
  assert.equal(
    await manager
      .getByRole("button", { name: "Crea gruppo" })
      .evaluate((el) => el === document.activeElement),
    true,
  );
  await manager.getByRole("button", { name: "Chiudi", exact: true }).click();
  await page
    .locator("#dictionary-filter")
    .getByText("Tutte le parole (3)", { exact: true })
    .waitFor();
  pass(
    "Group deletion reports retained/global counts, preserves shared members, recovers from error and resets deleted filter",
  );

  // The pointer left on the sheet's Chiudi hovers the toast and pauses it.
  await page.mouse.move(10, 10);
  await page.getByLabel("Cerca parole e varianti").fill("VOCE FLO");
  assert.equal(await page.locator("tbody tr").count(), 1);
  await row("VoiceFlow").waitFor();
  await page.getByText("1 parola trovata", { exact: true }).waitFor();
  await page.getByLabel("Cerca parole e varianti").fill("no-result-fixture");
  await page.getByText("Nessuna parola trovata", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Mostra tutte le parole" }).click();
  await page.evaluate(`{
    const base = window.dictionary.terms[0];
    window.dictionary.conflicts=[];
    window.dictionary.terms=Array.from({length:21},(_,i)=>({...base,id:100+i,text:'Termine '+String(i+1).padStart(2,'0'),variants:['alias '+(i+1)],groupIds:i<11?[2]:[]}));
  }`);
  await refresh();
  await page.getByText(/1–10 di 21 parole · Pagina 1 di 3/).waitFor();
  assert.equal(
    await page
      .getByRole("navigation", { name: "Pagine del dizionario" })
      .getByRole("button", { name: "Pagina 1", exact: true })
      .getAttribute("aria-current"),
    "page",
  );
  assert.equal(await page.locator("tbody tr").count(), 10);
  await page.setViewportSize({ width: 1060, height: 760 });
  await page.screenshot({ path: resolve(output, "dictionary-list-1060.png") });
  await page.getByRole("button", { name: "Successiva", exact: true }).click();
  await page.getByRole("button", { name: "Successiva", exact: true }).click();
  await rowAction("Termine 21", "Elimina");
  await discard.getByRole("button", { name: "Elimina", exact: true }).click();
  await discard.waitFor({ state: "hidden" });
  await page.getByText(/11–20 di 20 parole · Pagina 2 di 2/).waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "Successiva", exact: true })
      .isDisabled(),
    true,
  );
  await page.getByLabel("Cerca parole e varianti").fill("alias 1");
  await page.getByText(/1–10 di 11 parole · Pagina 1 di 2/).waitFor();
  await page.getByRole("button", { name: "Successiva", exact: true }).click();
  await page.locator("#dictionary-filter").click();
  await page.getByRole("option", { name: "Lavoro (11)", exact: true }).click();
  await page.getByText(/1–3 di 3 parole · Pagina 1 di 1/).waitFor();
  await page.getByLabel("Cerca parole e varianti").fill("");
  await page.locator("#dictionary-sort").click();
  await page.getByRole("option", { name: "Grafia Z–A", exact: true }).click();
  assert.equal(
    await page
      .locator("tbody tr")
      .first()
      .getByText("Termine 11", { exact: true })
      .count(),
    1,
  );
  await page.getByRole("button", { name: "Successiva", exact: true }).click();
  await openTerm("Termine 01");
  await dialog
    .getByLabel("Varianti confermate, una per riga")
    .fill("alias 1\nnuova variante");
  await dialog
    .getByRole("button", { name: "Conferma varianti e salva" })
    .click();
  await dialog.waitFor({ state: "hidden" });
  await page.getByText(/11–11 di 11 parole · Pagina 2 di 2/).waitFor();
  await page.setViewportSize({ width: 780, height: 620 });
  await page.getByRole("button", { name: "Precedente", exact: true }).click();
  await page
    .getByRole("heading", { name: "Dizionario", exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({ path: resolve(output, "dictionary-list-780.png") });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
    true,
  );
  pass(
    "Variant search, empty reset, stable descending sort, pagination/filter reset and last-page deletion clamp work; editing preserves list context",
  );
  await page.getByRole("link", { name: "Dettatura", exact: true }).click();
  await page.getByRole("link", { name: "Dizionario", exact: true }).click();
  await openTerm("Termine 01");
  await dialog
    .getByLabel("Grafia preferita", { exact: true })
    .fill("Bozza navigazione");
  await page.evaluate(() => window.history.back());
  await discard.getByRole("button", { name: "Continua a modificare" }).click();
  await discard.waitFor({ state: "hidden" });
  await page.waitForFunction(() => document.activeElement?.id === "term-text");
  assert.equal(
    await dialog.getByLabel("Grafia preferita", { exact: true }).inputValue(),
    "Bozza navigazione",
  );
  await page.evaluate(() => window.history.back());
  await discard.getByRole("button", { name: "Scarta modifiche" }).click();
  await page.waitForURL("https://voiceflow.test/#/");
  assert.equal(
    await page.evaluate(
      "window.dictionary.terms.some(t=>t.text==='Bozza navigazione')",
    ),
    false,
  );
  pass(
    "Back navigation guards the draft; keeping it stays in the editor and discard continues navigation",
  );
  await page.evaluate(() => {
    const w = window as any;
    w.dictionary.terms.push({
      id: 900,
      text: "TestFlight",
      groupIds: [],
      variants: ["tes flight"],
      language: "*",
      matching: "unicode-lowercase",
      context: { before: null, after: null, exceptBefore: [], exceptAfter: [] },
    });
    w.dictionary.version++;
    w.auto = [
      {
        spelling: "tes flight",
        termId: 900,
        term: "TestFlight",
        addedAt: 1750000000000,
        seenSessions: 3,
        changedSessions: 2,
        replayedSessions: 40,
      },
    ];
  });
  await page.getByRole("link", { name: "Dizionario", exact: true }).click();
  await refresh();
  const automatic = page.getByLabel("Varianti aggiunte in automatico");
  await automatic.getByText("«tes flight» → TestFlight").waitFor();
  await automatic
    .getByText(
      /Vista in 3 sessioni · avrebbe cambiato 2 sessioni su 40 salvate/,
    )
    .waitFor();
  await automatic.getByText("Automatica", { exact: true }).waitFor();
  await automatic
    .getByRole("button", { name: "Togli la variante automatica tes flight" })
    .click();
  await automatic.waitFor({ state: "hidden" });
  assert.deepEqual(
    await page.evaluate(
      "window.calls.filter(c=>c.cmd==='dictionary_remove_auto_variant').map(c=>c.args)",
    ),
    [{ spelling: "tes flight" }],
  );
  assert.equal(
    await page.evaluate(
      "window.dictionary.terms.find(t=>t.id===900).variants.length",
    ),
    0,
  );
  pass(
    "Automatic variants are labelled, show session and replay counts and leave with one action",
  );

  // Keyboard: ⌘F focuses search, Enter opens a focused row, ⌘Enter saves.
  await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
  await page.keyboard.press("Meta+f");
  await page.waitForFunction(
    () => document.activeElement?.id === "dictionary-search",
  );
  await row("Termine 06").focus();
  await page.keyboard.press("Enter");
  await dialog.getByRole("heading", { name: "Modifica parola" }).waitFor();
  await dialog
    .getByLabel("Varianti confermate, una per riga")
    .fill("alias 6\nsei");
  await page.keyboard.press("Meta+Enter");
  await dialog.waitFor({ state: "hidden" });
  assert.deepEqual(
    await page.evaluate("window.dictionary.terms.find(t=>t.id===105).variants"),
    ["alias 6", "sei"],
  );
  assert.equal(
    await row("Termine 06").evaluate((el) => el === document.activeElement),
    true,
  );
  await rowAction("Termine 06", "Duplica");
  await dialog.getByRole("heading", { name: "Duplica parola" }).waitFor();
  assert.equal(
    await dialog.getByLabel("Grafia preferita", { exact: true }).inputValue(),
    "Termine 06",
  );
  await dialog
    .getByLabel("Grafia preferita", { exact: true })
    .fill("Termine 06 bis");
  await dialog
    .getByLabel("Varianti confermate, una per riga")
    .fill("alias sei bis");
  await dialog
    .getByRole("button", { name: "Conferma varianti e salva" })
    .click();
  await dialog.waitFor({ state: "hidden" });
  const copy = await page.evaluate(
    "window.dictionary.terms.find(t=>t.text==='Termine 06 bis')",
  );
  assert.deepEqual(copy.groupIds, [2]);
  assert.deepEqual(copy.variants, ["alias sei bis"]);
  assert.notEqual(copy.id, 105);
  pass(
    "⌘F focuses search, Enter opens a focused row, ⌘Enter saves and Duplica creates a separate term",
  );

  // Bulk actions sequence single-term commands and report partial failures.
  await page.getByRole("checkbox", { name: "Seleziona Termine 02" }).click();
  await page.getByRole("checkbox", { name: "Seleziona Termine 03" }).click();
  const bulk = page.getByRole("region", {
    name: "Azioni sulle parole selezionate",
  });
  await bulk.getByText("2 selezionate", { exact: true }).waitFor();
  assert.equal(await dialog.count(), 0);
  let saves = await count("dictionary_save_term");
  await bulk.getByRole("button", { name: "Assegna a gruppo" }).click();
  await page
    .getByRole("menuitem", { name: "Cliente nuovo", exact: true })
    .click();
  await page
    .getByText("2 parole assegnate a “Cliente nuovo”", { exact: true })
    .waitFor();
  assert.deepEqual(
    await page.evaluate(
      "[101,102].map(id=>window.dictionary.terms.find(t=>t.id===id).groupIds)",
    ),
    [
      [2, 3],
      [2, 3],
    ],
  );
  assert.equal(await count("dictionary_save_term"), saves + 2);
  await bulk.waitFor({ state: "hidden" });
  pass("Bulk assignment saves each selected term and clears the selection");

  await page
    .getByRole("checkbox", { name: "Seleziona le parole della pagina" })
    .click();
  await bulk.getByText("10 selezionate", { exact: true }).waitFor();
  await page.evaluate("window.failIds=[103]");
  saves = await count("dictionary_save_term");
  await bulk.getByRole("button", { name: "Rimuovi da gruppo" }).click();
  await page.getByRole("menuitem", { name: "Lavoro", exact: true }).click();
  await page
    .getByText("1 parola su 10 non aggiornata", { exact: true })
    .waitFor();
  await page
    .getByText(/9 parole rimosse da “Lavoro”\. Scrittura non riuscita/)
    .waitFor();
  assert.equal(await count("dictionary_save_term"), saves + 10);
  assert.deepEqual(
    await page.evaluate(
      "window.dictionary.terms.filter(t=>t.groupIds.includes(2)).map(t=>t.id).sort((a,b)=>a-b)",
    ),
    // Termine 04 failed; Termine 10 and 11 were not selected (page 2).
    [103, 109, 110],
  );
  await bulk.getByText("1 selezionata", { exact: true }).waitFor();
  assert.equal(
    await page
      .getByRole("checkbox", { name: "Seleziona Termine 04" })
      .getAttribute("aria-checked"),
    "true",
  );
  pass(
    "Bulk removal continues past a failed term, reports counts and keeps only the failed term selected",
  );

  await page.evaluate("window.failIds=[]");
  await page.getByRole("checkbox", { name: "Seleziona Termine 05" }).click();
  await bulk.getByText("2 selezionate", { exact: true }).waitFor();
  await bulk.getByRole("button", { name: "Elimina", exact: true }).click();
  const confirmBulk = page.getByRole("alertdialog", {
    name: "Eliminare 2 parole?",
  });
  await confirmBulk.getByRole("button", { name: "Annulla" }).click();
  await confirmBulk.waitFor({ state: "hidden" });
  assert.equal(await count("dictionary_delete_term"), 1);
  await bulk.getByRole("button", { name: "Elimina", exact: true }).click();
  await confirmBulk
    .getByRole("button", { name: "Elimina", exact: true })
    .click();
  await confirmBulk.waitFor({ state: "hidden" });
  await page.getByText("2 parole eliminate", { exact: true }).waitFor();
  assert.equal(await count("dictionary_delete_term"), 3);
  assert.equal(
    await page.evaluate(
      "window.dictionary.terms.some(t=>t.id===103||t.id===104)",
    ),
    false,
  );
  await bulk.waitFor({ state: "hidden" });
  await page.waitForFunction(
    () => document.activeElement?.id === "dictionary-search",
  );
  pass(
    "Bulk deletion asks for confirmation, deletes each selected term and returns focus to search",
  );

  assert.equal(
    await page.evaluate(
      "window.calls.some(c=>c.cmd==='stop'||c.cmd==='cancel'||c.cmd==='start')",
    ),
    false,
  );
  assert.deepEqual(errors, []);
  pass("No unintended dictation commands or browser errors");
  await writeFile(
    resolve(output, "result.json"),
    JSON.stringify({ checks, errors }, null, 2),
  );
} finally {
  await browser.close();
}
