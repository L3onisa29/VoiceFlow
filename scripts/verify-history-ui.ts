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
const output = resolve(root, "artifacts/history-b1");
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
window.state = {revision:1,phase:'recording',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:3,device:'Fixture',settings:{language:'italian',format:'spoken-commands',cleanup:true},transcript:{confirmed:'sessione attiva',partial:'',audioSeconds:3,inferenceSeconds:0,chunks:1,finished:false}};
const entry = {id:2,createdAt:1750000000000,text:'voce flo non 42',wordCount:4,durationSeconds:12,language:'it-IT'};
const metadata = {modelId:'nvidia/nemotron-3.5-asr-streaming-0.6b',modelRevision:'fixture-revision',modelProvenance:'fixture',decoderVersion:'fixture',processorVersion:'voiceflow-text-v1',format:'spoken-commands',cleanup:true,groups:[],dictionaryVersion:null,cleanupVersion:'builtin-v1'};
const details = {
  2:{entry,originalAsr:['<unk> voce flo non 42'],producedText:entry.text,processing:metadata,editVersion:0,currentRevision:null,revisions:[]},
  1:{entry:{...entry,id:1,text:'Testo precedente'},originalAsr:null,producedText:'Testo precedente',processing:null,editVersion:0,currentRevision:null,revisions:[]}
};
window.details = details;
mockIPC(async (cmd,args) => {
  window.calls.push({cmd,args});
  if(cmd === 'get_state') return structuredClone(window.state);
  if(cmd === 'get_permissions') return {microphone:true,microphoneStatus:'authorized',accessibility:true};
  if(cmd === 'dictionary_snapshot') return {terms:[],groups:[],selectedGroupIds:[],version:1,conflicts:[]};
  if(cmd === 'history_page') return {entries:Object.values(details).map(d=>structuredClone(d.entry)).reverse(),nextCursor:null,stats:{totalWords:Object.keys(details).length*4,totalSessions:Object.keys(details).length,totalSeconds:Object.keys(details).length*12,wordsPerMinute:20}};
  if(cmd === 'history_detail') return structuredClone(details[args.id]);
  if(cmd === 'history_copy') return;
  if(cmd === 'history_delete') {delete details[args.id]; return;}
  const r = args.request;
  const d = r && details[r.id];
  if(cmd === 'history_edit') {
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
  await page.goto("https://voiceflow.test/");
  await page
    .getByRole("button", { name: "Apri testo", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Testo salvato", { exact: true }).waitFor();
  assert.equal(
    await dialog.getByLabel("Originale ASR", { exact: true }).inputValue(),
    "<unk> voce flo non 42",
  );
  assert.equal(
    await dialog.getByLabel("Risultato elaborato originale").inputValue(),
    "voce flo non 42",
  );
  assert.equal(
    await dialog
      .getByRole("button", { name: "Ricorda questa correzione", exact: true })
      .count(),
    0,
  );
  pass(
    "Raw ASR and produced text are separate; no confirmation before user edit",
  );

  await dialog
    .getByLabel("Testo salvato", { exact: true })
    .fill("VoiceFlow non 42");
  await page.evaluate("window.failEdit = true");
  await dialog
    .getByRole("button", { name: "Salva correzione", exact: true })
    .click();
  await dialog
    .getByText("Error: Scrittura non riuscita", { exact: true })
    .waitFor();
  assert.equal(
    await dialog.getByLabel("Testo salvato", { exact: true }).inputValue(),
    "VoiceFlow non 42",
  );
  await page.evaluate("window.failEdit = false");
  await dialog
    .getByRole("button", { name: "Salva correzione", exact: true })
    .click();
  await dialog
    .getByText(
      "Correzione salvata. Nessuna sostituzione automatica attivata.",
      { exact: true },
    )
    .waitFor();
  const keys = await page.evaluate(
    "window.calls.filter(c => c.cmd === 'history_edit').map(c => c.args.request.requestKey)",
  );
  assert.equal(keys[0], keys[1]);
  assert.equal(
    await page.evaluate(
      "window.calls.filter(c => c.cmd === 'history_remember').length",
    ),
    0,
  );
  assert.equal(
    await dialog
      .getByRole("button", { name: "Ricorda questa correzione", exact: true })
      .isDisabled(),
    true,
  );
  pass(
    "Write error preserves draft; retry reuses idempotency key; save never auto-remembers",
  );

  await dialog
    .getByRole("radio", { name: "Riconoscimento", exact: true })
    .click();
  await dialog
    .getByRole("button", { name: "Ricorda questa correzione", exact: true })
    .click();
  await dialog
    .getByText("Correzione ricordata. Le regole attive non sono cambiate.", {
      exact: true,
    })
    .waitFor();
  assert.equal(
    await page.evaluate("window.details[2].revisions[0].category"),
    "recognition",
  );
  await dialog
    .getByRole("button", { name: "Copia testo salvato", exact: true })
    .click();
  assert.equal(
    await page.evaluate(
      "window.calls.filter(c => c.cmd === 'history_copy').at(-1).args.id",
    ),
    2,
  );
  await page.screenshot({ path: resolve(output, "correction-1060.png") });
  pass(
    "Explicit category and remember action; copy requests current saved session text",
  );

  await dialog
    .getByLabel("Testo salvato", { exact: true })
    .fill("Una riscrittura libera");
  assert.equal(
    await dialog
      .getByRole("button", { name: "Annulla ultima correzione", exact: true })
      .isDisabled(),
    true,
  );
  await dialog
    .getByRole("button", { name: "Scarta modifiche", exact: true })
    .click();
  await dialog
    .getByRole("button", { name: "Annulla ultima correzione", exact: true })
    .click();
  await dialog
    .getByText("Correzione annullata e relativa conferma rimossa.", {
      exact: true,
    })
    .waitFor();
  assert.equal(
    await dialog.getByLabel("Testo salvato", { exact: true }).inputValue(),
    "voce flo non 42",
  );
  assert.equal(
    await page.evaluate("window.details[2].revisions[0].category"),
    null,
  );
  pass(
    "Unsaved draft cannot be undone accidentally; undo restores text and removes remembered evidence",
  );

  await page.setViewportSize({ width: 780, height: 620 });
  await page.screenshot({ path: resolve(output, "history-780.png") });
  assert.equal(
    await page.evaluate("document.documentElement.scrollWidth <= innerWidth"),
    true,
  );
  const bounds = await dialog.boundingBox();
  assert.ok(bounds && bounds.y >= 0 && bounds.y + bounds.height <= 620);
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "Apri testo", exact: true })
    .nth(1)
    .click();
  await page
    .getByText(
      "Originale ASR non disponibile per questa trascrizione precedente.",
      { exact: true },
    )
    .waitFor();
  await page.keyboard.press("Escape");
  pass("Dialog fits 780x620 viewport; legacy raw absence is explicit");

  await page.getByRole("link", { name: "Dizionario", exact: true }).click();
  await page
    .getByRole("heading", { name: "Dizionario", exact: true })
    .waitFor();
  await page.getByRole("link", { name: "Dettatura", exact: true }).click();
  await page.getByRole("button", { name: "Pausa", exact: true }).waitFor();
  assert.equal(await page.evaluate("window.state.phase"), "recording");
  assert.equal(
    await page.evaluate(
      "window.calls.filter(c => ['stop_recording','cancel_recording','toggle_recording'].includes(c.cmd)).length",
    ),
    0,
  );
  pass("Editing and navigation preserve the active recording session");

  await page
    .getByRole("button", { name: /Elimina trascrizione/ })
    .first()
    .click();
  await page
    .getByRole("alertdialog")
    .getByText(/Originale, risultato, revisioni ed esempi ricordati/)
    .waitFor();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Annulla", exact: true })
    .click();
  assert.equal(
    await page.evaluate(
      "window.calls.filter(c => c.cmd === 'history_delete').length",
    ),
    0,
  );
  assert.deepEqual(errors, []);
  pass(
    "Deletion states local scope and derived-data policy; cancel does not delete; no browser errors",
  );
} finally {
  await browser.close();
}
await writeFile(
  resolve(output, "result.json"),
  JSON.stringify(
    {
      checks,
      screenshots: ["correction-1060.png", "history-780.png"],
      backend: "Tauri IPC fixtures; SQLite tested separately in Rust",
    },
    null,
    2,
  ),
);
