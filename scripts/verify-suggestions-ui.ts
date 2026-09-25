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
const output = resolve(root, "artifacts/suggestions-c2");
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
const ctx={before:null,after:null,exceptBefore:[],exceptAfter:[]};
const candidate={definition:{category:'terminology',preferred:'VoiceFlow',variants:['voice flow'],matching:'exact',context:ctx},language:'it',groupIds:[1],reason:'Grafia del progetto',positiveExamples:[{input:'voice flow',output:'VoiceFlow'}],negativeExamples:[{input:'voice flower',output:'voice flower'}]};
const evidence={sessionId:7,revisionId:11,originalIndex:null,before:'uso voice flow',after:'uso VoiceFlow',humanConfirmed:true};
window.review={version:0,dictionaryVersion:3,groups:[{id:1,name:'Progetto'}],publications:[],proposals:[{id:1,revision:1,candidate,state:'proposed',rejectionReason:null,approvedAt:null,conflicts:[],evidence:[evidence]},{id:2,revision:1,candidate:{...candidate,definition:{category:'cleanup',operation:{kind:'remove-false-start',source:['volevo','vorrei']},context:ctx},reason:'Falsa partenza',positiveExamples:[{input:'volevo vorrei chiedere',output:'vorrei chiedere'}],negativeExamples:[{input:'volevo chiedere',output:'volevo chiedere'}]},state:'proposed',rejectionReason:null,approvedAt:null,conflicts:[],evidence:[{sessionId:8,revisionId:null,originalIndex:0,before:'volevo vorrei chiedere',after:null,humanConfirmed:false}]},{id:3,revision:1,candidate:{...candidate,definition:{...candidate.definition,preferred:'Conflitto'}},state:'proposed',rejectionReason:null,approvedAt:null,conflicts:['Conflitto con il dizionario manuale.'],evidence:[evidence]}]};
mockIPC(async(cmd,args)=>{
 window.calls.push({cmd,args});
 if(cmd==='get_state')return {revision:1,phase:'recording',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:3,device:'Fixture',settings:{language:'italian',format:'spoken-commands',cleanup:true},transcript:{confirmed:'sessione attiva',partial:'',audioSeconds:3,inferenceSeconds:0,chunks:1,finished:false}};
 if(cmd==='get_permissions')return {microphone:true,microphoneStatus:'authorized',accessibility:true};
 if(cmd==='history_page')return {entries:[],nextCursor:null,stats:{totalWords:0,totalSessions:0,totalSeconds:0,wordsPerMinute:0}};
 if(cmd==='dictionary_snapshot')return {terms:[],groups:[{id:1,name:'Progetto'}],selectedGroupIds:[1],version:3,conflicts:[]};
 if(cmd==='suggestions_snapshot')return structuredClone(window.review);
 if(cmd==='suggestions_review'){
  if(window.failSave)throw new Error('Scrittura non riuscita. Riprova.');
  const request=args.request;const p=window.review.proposals.find(p=>p.id===request.id);const action=request.action;
  if(action.kind==='edit'){p.candidate=action.candidate;p.state='proposed';}
  if(action.kind==='reject'){p.state='rejected';p.rejectionReason=action.reason;}
  if(action.kind==='defer')p.state='deferred';
  if(action.kind==='approve'){p.state='approved';p.approvedAt=1000;}
  if(action.kind==='disable')p.state='disabled';
  if(action.kind==='restore'){const ids=window.review.publications.find(v=>v.version===action.version)?.proposalIds??[];window.review.proposals.forEach(p=>{if(p.approvedAt!==null)p.state=ids.includes(p.id)?'approved':'disabled';});}
  if(['approve','disable','restore'].includes(action.kind)){const previousVersion=window.review.version;window.review.version++;window.review.publications.unshift({version:window.review.version,previousVersion,action:action.kind,createdAt:1000,proposalIds:window.review.proposals.filter(p=>p.state==='approved').map(p=>p.id)});}
  if(p)p.revision++;
  return structuredClone(window.review);
 }
 return null;
});`,
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
const filter = async (label: string) => {
  await page.getByLabel("Mostra", { exact: true }).click();
  await page.getByRole("option", { name: label, exact: true }).click();
};
try {
  await page.goto("https://voiceflow.test/#/suggestions");
  await page
    .getByRole("heading", { name: "Suggerimenti", exact: true })
    .waitFor();
  const card = page.locator('[data-proposal-id="1"]');
  const cleanup = page.locator('[data-proposal-id="2"]');
  await card.getByText("VoiceFlow", { exact: true }).first().waitFor();
  await card.locator("summary").first().click();
  await card
    .getByText("Sessione #7 · Revisione #11", { exact: true })
    .waitFor();
  await card
    .getByText("Correzione umana confermata", { exact: true })
    .waitFor();
  await cleanup.locator("summary").first().click();
  await cleanup
    .getByText("Fonte osservata, ipotesi da valutare", { exact: true })
    .waitFor();
  assert.equal(
    await page
      .locator('[data-proposal-id="3"]')
      .getByRole("button", { name: "Approva", exact: true })
      .isDisabled(),
    true,
  );
  assert.equal(
    await page.evaluate("window.calls.some(c=>c.cmd==='suggestions_review')"),
    false,
  );
  await page.screenshot({
    path: resolve(output, "review-1060.png"),
    fullPage: true,
  });
  pass(
    "Real source identifiers, human correction versus hypothesis, truthful counts, conflict blocking, no activation on read",
  );
  await card.getByRole("button", { name: "Approva", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByText(/definizione approvata e le sue versioni restano/)
    .waitFor();
  await dialog.getByRole("button", { name: "Annulla", exact: true }).click();
  assert.equal(
    await page.evaluate("window.calls.some(c=>c.cmd==='suggestions_review')"),
    false,
  );
  await card.getByRole("button", { name: "Approva", exact: true }).click();
  await page.evaluate("window.failSave=true");
  await dialog
    .getByRole("button", { name: "Approva e pubblica", exact: true })
    .click();
  await dialog.getByText(/Scrittura non riuscita/).waitFor();
  await page.evaluate("window.failSave=false");
  await dialog
    .getByRole("button", { name: "Approva e pubblica", exact: true })
    .click();
  await dialog.waitFor({ state: "hidden" });
  const calls = await page.evaluate(
    "window.calls.filter(c=>c.cmd==='suggestions_review').map(c=>c.args.request)",
  );
  assert.equal(calls[0].requestKey, calls[1].requestKey);
  assert.equal(calls[0].expectedVersion, 0);
  assert.equal(calls[0].dictionaryVersion, 3);
  await page
    .getByRole("status")
    .filter({ hasText: "Versione 1 pubblicata" })
    .waitFor();
  pass(
    "Explicit approval policy, cancel without write, visible write failure and idempotent retry",
  );
  await filter("Approvate e attive");
  await card.getByRole("button", { name: "Disattiva", exact: true }).click();
  await filter("Disattivate");
  await card.getByRole("button", { name: "Riattiva", exact: true }).waitFor();
  await page
    .getByRole("button", { name: "Ripristina versione 1", exact: true })
    .click();
  await dialog
    .getByRole("button", { name: "Conferma ripristino", exact: true })
    .click();
  await dialog.waitFor({ state: "hidden" });
  await filter("Approvate e attive");
  await card.getByText("Approvata · attiva", { exact: true }).waitFor();
  pass(
    "Disable and restore previous publication remain distinct from approval history",
  );
  await filter("Da valutare e rimandate");
  await cleanup.getByRole("button", { name: "Modifica", exact: true }).click();
  await page.setViewportSize({ width: 780, height: 620 });
  await dialog
    .getByLabel("Espressione completa, da 2 a 7 parole", { exact: true })
    .fill("volevo vorrei");
  await dialog
    .getByLabel("Motivazione", { exact: true })
    .fill("Esempio rivisto dalla persona");
  await dialog
    .getByRole("button", { name: "Aggiungi esempio", exact: true })
    .first()
    .click();
  await dialog
    .getByRole("button", { name: "Rimuovi esempio 2", exact: true })
    .click();
  assert.equal(
    await dialog
      .getByRole("button", { name: "Rimuovi esempio 1", exact: true })
      .isDisabled(),
    true,
  );
  await dialog
    .getByLabel("Motivazione", { exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({ path: resolve(output, "edit-780.png") });
  const bounds = await dialog.boundingBox();
  assert.ok(bounds && bounds.y >= 0 && bounds.y + bounds.height <= 620);
  await dialog
    .getByRole("button", { name: "Salva proposta", exact: true })
    .click();
  await dialog.waitFor({ state: "hidden" });
  assert.equal(await page.evaluate("window.review.version"), 3);
  await cleanup
    .getByText("Esempio rivisto dalla persona", { exact: true })
    .waitFor();
  await cleanup.getByRole("button", { name: "Rimanda", exact: true }).click();
  await cleanup.getByText("Rimandata", { exact: true }).waitFor();
  await cleanup.getByRole("button", { name: "Rifiuta", exact: true }).click();
  assert.equal(
    await dialog
      .getByRole("button", { name: "Conferma rifiuto", exact: true })
      .isDisabled(),
    true,
  );
  await dialog
    .getByLabel("Motivo del rifiuto", { exact: true })
    .fill("Ripetizione intenzionale");
  await dialog
    .getByRole("button", { name: "Conferma rifiuto", exact: true })
    .click();
  await dialog.waitFor({ state: "hidden" });
  await filter("Rifiutate");
  await cleanup
    .getByText("Motivo del rifiuto precedente: Ripetizione intenzionale", {
      exact: true,
    })
    .waitFor();
  assert.equal(
    await cleanup.getByRole("button", { name: "Approva", exact: true }).count(),
    0,
  );
  pass(
    "Structured cleanup editing works at 780×620, saving never activates, defer and mandatory rejection reason persist",
  );
  await page.evaluate("window.review.proposals[0].evidence=[]");
  await page.getByRole("button", { name: "Aggiorna", exact: true }).click();
  await filter("Approvate e attive");
  await card.locator("summary").first().click();
  await card.getByText(/Le sessioni sorgenti sono state eliminate/).waitFor();
  await page.screenshot({
    path: resolve(output, "active-780.png"),
    fullPage: true,
  });
  pass(
    "Removed evidence shows zero references while approved independent definition remains visible",
  );
  assert.equal(
    await page.evaluate(
      "window.calls.some(c=>['stop_recording','cancel_recording','toggle_recording','pause_recording'].includes(c.cmd))",
    ),
    false,
  );
  assert.deepEqual(errors, []);
  pass(
    "Navigation preserves simulated recording; no unexpected commands or browser errors",
  );
  await writeFile(
    resolve(output, "result.json"),
    JSON.stringify({ checks, errors }, null, 2),
  );
} finally {
  await browser.close();
}
