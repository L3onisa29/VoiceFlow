// Production UI verification with Tauri IPC fixtures. No HTTP listener or audio.
// Pass an installed Playwright module through PLAYWRIGHT_MODULE.
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const modulePath = process.env.PLAYWRIGHT_MODULE;
if (!modulePath)
  throw new Error("Set PLAYWRIGHT_MODULE to an installed Playwright index.mjs");
const { chromium } = await import(pathToFileURL(modulePath).href);
const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "artifacts/dictionary-b2");
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
window.calls=[];window.failSave=false;
const empty={variants:[],language:'*',matching:'exact',context:{before:null,after:null,exceptBefore:[],exceptAfter:[]}};
window.dictionary={terms:[{id:1,text:'VoiceFlow',groupIds:[1],...empty,variants:['voice flow']},{id:2,text:'Promemoria',groupIds:[],...empty}],groups:[{id:1,name:'Progetto'},{id:2,name:'Lavoro'}],selectedGroupIds:[1],version:3,conflicts:[]};
mockIPC(async(cmd,args)=>{
  window.calls.push({cmd,args});
  if(cmd==='get_state')return {revision:1,phase:'recording',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:3,device:'Fixture',settings:{language:'italian',format:'spoken-commands',cleanup:true},transcript:{confirmed:'sessione attiva',partial:'',audioSeconds:3,inferenceSeconds:0,chunks:1,finished:false}};
  if(cmd==='get_permissions')return {microphone:true,microphoneStatus:'authorized',accessibility:true};
  if(cmd==='history_page')return {entries:[],nextCursor:null,stats:{totalWords:0,totalSessions:0,totalSeconds:0,wordsPerMinute:0}};
  if(cmd==='dictionary_snapshot')return structuredClone(window.dictionary);
  if(cmd==='dictionary_select_groups'){window.dictionary.selectedGroupIds=args.ids;window.dictionary.version++;return structuredClone(window.dictionary);}
  if(cmd==='dictionary_save_term'){
    if(window.failSave)throw new Error('Scrittura non riuscita');
    const input=args.input;
    const term={...input,id:input.id??3};
    window.dictionary.terms=window.dictionary.terms.filter(t=>t.id!==term.id).concat([term]);window.dictionary.version++;
    return structuredClone(window.dictionary);
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
    .getByText("Nessuna variante, non applicata", { exact: true })
    .waitFor();
  const group = page.locator("#active-group-2");
  await group.click();
  assert.deepEqual(
    await page.evaluate("window.dictionary.selectedGroupIds"),
    [1, 2],
  );
  await page.getByText(/Versione 4/).waitFor();
  pass(
    "Manual groups persist through IPC while the active session remains visible",
  );
  await page
    .getByRole("button", { name: "Modifica VoiceFlow", exact: true })
    .click();
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
  await dialog
    .getByText("Error: Scrittura non riuscita", { exact: true })
    .waitFor();
  assert.equal(
    await dialog.getByLabel("Varianti confermate, una per riga").inputValue(),
    "voice flow\nvoce flo",
  );
  await page.evaluate("window.failSave=false");
  await page.screenshot({ path: resolve(output, "dictionary-edit-1060.png") });
  await dialog
    .getByRole("button", { name: "Conferma varianti e salva" })
    .click();
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
  await page.evaluate("window.dictionary.conflicts=[1,2]");
  await page
    .getByRole("button", { name: "Aggiorna dizionario", exact: true })
    .click();
  await page.getByText(/Varianti in conflitto tra 2 termini/).waitFor();
  assert.equal(await page.getByText("Conflitto", { exact: true }).count(), 2);
  pass(
    "Unresolved conflicts are visible in management and marked on the affected terms",
  );
  await page.setViewportSize({ width: 780, height: 620 });
  await page
    .getByRole("button", { name: "Modifica VoiceFlow", exact: true })
    .click();
  await dialog.waitFor();
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
  assert.deepEqual(
    await page.evaluate(
      "window.dictionary.terms.find(t=>t.text==='Solo nota').variants",
    ),
    [],
  );
  pass(
    "Small-window editor stays usable; a term without variants stays a note",
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
