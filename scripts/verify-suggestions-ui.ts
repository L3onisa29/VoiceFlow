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
const output = resolve(root, "artifacts/suggestions-disfluencies");
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
 if(cmd==='suggestions_replay')return {sessions:40,version:window.review.version,rules:[{proposalId:1,active:window.review.proposals[0].state==='approved',changedSessions:3,excerpts:[{sessionId:7,before:'uso voice flow oggi',after:'uso VoiceFlow oggi'}],error:null,atPublication:null,usage:window.review.proposals[0].state==='approved'?{activations:0,sessionsWithActivation:0,trackedSessions:12}:null},{proposalId:2,active:false,changedSessions:0,excerpts:[],error:null,atPublication:null,usage:null}]};
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
  await page.getByRole("combobox", { name: "Mostra", exact: true }).click();
  await page.getByRole("option", { name: label, exact: true }).click();
};
const row = (id: number) => page.locator(`[data-proposal-id="${id}"]`);
const detail = page.locator("[data-proposal-detail]");
const dialog = page.getByRole("dialog");
const toasts = page.getByRole("region", { name: /Notifiche/ });
const reviewCalls = () =>
  page.evaluate(
    "window.calls.filter(c=>c.cmd==='suggestions_review').length",
  ) as Promise<number>;
const openSection = (name: RegExp | string) =>
  detail.getByRole("button", { name, exact: typeof name === "string" }).click();
try {
  await page.goto("https://voiceflow.test/#/review");
  await page
    .getByRole("heading", { level: 1, name: "Revisione", exact: true })
    .waitFor();
  assert.equal(
    await page
      .getByRole("tab", { name: "Proposte", exact: true })
      .getAttribute("aria-selected"),
    "true",
  );
  const queue = page.getByRole("region", { name: "Coda delle proposte" });
  await queue.getByText("3 proposte", { exact: true }).waitFor();
  assert.equal(
    await page.getByRole("button", { name: "Aggiorna", exact: true }).count(),
    0,
  );
  // Master–detail: the first proposal of the queue is open without a link.
  await row(1).getByText("voice flow → VoiceFlow", { exact: true }).waitFor();
  await row(1).getByText("Terminologia", { exact: true }).waitFor();
  await detail
    .getByRole("heading", { level: 2, name: "VoiceFlow", exact: true })
    .waitFor();
  assert.equal(await detail.getAttribute("data-proposal-detail"), "1");
  await detail.getByText("Confidenza sconosciuta", { exact: true }).waitFor();
  // The confidence disclaimer is explained once, on demand.
  assert.equal(
    await page
      .getByText("La confidenza è una stima del provider", { exact: false })
      .count(),
    0,
  );
  await detail
    .getByRole("button", { name: "Come leggere la confidenza", exact: true })
    .hover();
  await page
    .getByText("La confidenza è una stima del provider", { exact: false })
    .waitFor();
  await page.mouse.move(0, 0);
  await openSection(/^1 riferimento · 1 sessione · 1 correzione umana/);
  await detail
    .getByText("Sessione #7 · Revisione #11", { exact: true })
    .waitFor();
  await detail
    .getByText("Correzione umana confermata", { exact: true })
    .waitFor();
  await row(2).click();
  await page.waitForURL(/proposal=2/);
  await detail
    .getByRole("heading", { name: "Falsa partenza", exact: true })
    .waitFor();
  await openSection(/^1 riferimento · 1 sessione · 0 correzioni umane/);
  await detail
    .getByText("Fonte osservata, ipotesi da valutare", { exact: true })
    .waitFor();
  await row(3).click();
  await detail.getByText("Pubblicazione bloccata", { exact: true }).waitFor();
  assert.equal(
    await detail
      .getByRole("button", { name: "Approva", exact: true })
      .isDisabled(),
    true,
  );
  // A blocked approval stays blocked from the keyboard too.
  await page.keyboard.press("a");
  assert.equal(await dialog.count(), 0);
  assert.equal(await reviewCalls(), 0);
  await page.screenshot({
    path: resolve(output, "review-1060.png"),
    fullPage: true,
  });
  pass(
    "Master–detail queue with real source identifiers, human correction versus hypothesis, truthful counts, conflict blocking, confidence explained once, no activation on read",
  );
  await row(1).click();
  await detail
    .getByRole("heading", { name: "VoiceFlow", exact: true })
    .waitFor();
  const replayed = detail.getByLabel("Riprova sullo storico");
  await replayed
    .getByText("Riprova sullo storico: cambia 3 sessioni su 40.", {
      exact: true,
    })
    .waitFor();
  await replayed
    // One excerpt is stored for the three changed sessions.
    .getByRole("button", {
      name: "Estratti prima e dopo (1 di 3)",
      exact: true,
    })
    .click();
  await replayed.getByText("uso VoiceFlow oggi").waitFor();
  await row(2).click();
  await detail
    .getByText(
      "Riprova sullo storico: non cambia nessuna sessione salvata (40 riprovate).",
      { exact: true },
    )
    .waitFor();
  await detail
    .getByText(/senza effetto sullo storico non viene pubblicata in automatico/)
    .waitFor();
  assert.equal(await reviewCalls(), 0);
  pass(
    "History replay shows per-rule session counts and excerpts without any write",
  );
  await row(1).click();
  await detail
    .getByRole("heading", { name: "VoiceFlow", exact: true })
    .waitFor();
  await detail.getByRole("button", { name: "Approva", exact: true }).click();
  await dialog
    .getByText(/definizione approvata e le sue versioni restano/)
    .waitFor();
  await dialog
    .getByText("Riprova sullo storico: cambia 3 sessioni su 40.", {
      exact: true,
    })
    .waitFor();
  await dialog.getByRole("button", { name: "Annulla", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  assert.equal(await reviewCalls(), 0);
  // The A shortcut opens the same confirmation; it never publishes directly.
  await page.keyboard.press("a");
  await dialog
    .getByRole("heading", { name: "Approva e pubblica", exact: true })
    .waitFor();
  assert.equal(await reviewCalls(), 0);
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
  assert.equal(calls.length, 2);
  assert.equal(calls[0].requestKey, calls[1].requestKey);
  assert.equal(calls[0].expectedVersion, 0);
  assert.equal(calls[0].dictionaryVersion, 3);
  await toasts.getByText("Versione 1 pubblicata", { exact: true }).waitFor();
  // The queue moves on to the next proposal to review.
  await page.waitForURL(/proposal=2/);
  await detail
    .getByRole("heading", { name: "Falsa partenza", exact: true })
    .waitFor();
  pass(
    "Explicit approval policy (button and A shortcut), cancel without write, visible write failure, idempotent retry, toast and advance to next",
  );
  await filter("Approvate e attive");
  await row(1).click();
  await detail.getByRole("button", { name: "Disattiva", exact: true }).click();
  await toasts.getByText("Versione 2 pubblicata", { exact: true }).waitFor();
  await filter("Disattivate");
  await row(1).click();
  await detail.getByRole("button", { name: "Riattiva", exact: true }).waitFor();
  const versions = page.getByRole("region", { name: "Versioni pubblicate" });
  await versions.locator('[data-publication-version="1"]').waitFor();
  await versions
    .getByRole("button", { name: "Azioni versione 1", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Ripristina versione 1", exact: true })
    .click();
  await dialog
    .getByRole("button", { name: "Conferma ripristino", exact: true })
    .click();
  await dialog.waitFor({ state: "hidden" });
  await toasts.getByText("Versione 3 pubblicata", { exact: true }).waitFor();
  await filter("Approvate e attive");
  await row(1).click();
  await detail.getByText("Approvata · attiva", { exact: true }).waitFor();
  pass(
    "Disable and restore previous publication remain distinct from approval history",
  );
  await filter("Da revisionare");
  await row(2).click();
  await detail.getByRole("button", { name: "Modifica", exact: true }).click();
  await page.setViewportSize({ width: 780, height: 620 });
  await dialog
    .getByLabel("Espressione completa, da 2 a 7 parole", { exact: true })
    .fill("volevo vorrei");
  await dialog.getByLabel("Operazione di pulizia", { exact: true }).click();
  await page
    .getByRole("option", {
      name: "Ripetizione di una frase breve",
      exact: true,
    })
    .click();
  await dialog
    .getByLabel("Frase ripetuta, da 2 a 3 parole (richiede contesto)", {
      exact: true,
    })
    .fill("le ultime");
  await dialog
    .getByLabel("Solo prima della parola", { exact: true })
    .fill("chat");
  await dialog
    .locator("#positiveExamples-0-input")
    .fill("le ultime le ultime chat");
  await dialog.locator("#positiveExamples-0-output").fill("le ultime chat");
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
  // Letters typed in the editor never trigger the review shortcuts.
  const beforeEdit = await reviewCalls();
  await dialog.getByLabel("Motivazione", { exact: true }).press("r");
  await dialog.getByLabel("Motivazione", { exact: true }).press("Backspace");
  assert.equal(await reviewCalls(), beforeEdit);
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
  await toasts.getByText("Proposta salvata", { exact: true }).waitFor();
  assert.equal(await page.evaluate("window.review.version"), 3);
  // Under the two-pane width the linked proposal is shown alone.
  assert.equal(await queue.isVisible(), false);
  await detail
    .getByText("Esempio rivisto dalla persona", { exact: true })
    .waitFor();
  assert.deepEqual(
    await page.evaluate(
      "window.review.proposals.find(p => p.id === 2).candidate.definition.operation",
    ),
    { kind: "repeat-phrase", phrase: ["le", "ultime"] },
  );
  await detail
    .getByRole("heading", { name: "Ripetizione di frase", exact: true })
    .waitFor();
  await detail
    .getByText(
      "Elimina la prima occorrenza di “le ultime” quando ripetuta due volte.",
      { exact: true },
    )
    .waitFor();
  pass(
    "Phrase repetition edits preserve the new shape and context without activation",
  );
  // R defers and moves to the next item; K goes back; X opens the rejection.
  await page.keyboard.press("r");
  await toasts.getByText("Proposta rimandata", { exact: true }).waitFor();
  await page.waitForURL(/proposal=3/);
  await page.keyboard.press("k");
  await page.waitForURL(/proposal=2/);
  await detail.getByText("Rimandata", { exact: true }).waitFor();
  await page.keyboard.press("x");
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
  await toasts.getByText("Proposta rifiutata", { exact: true }).waitFor();
  assert.deepEqual(
    await page.evaluate(
      "window.calls.filter(c=>c.cmd==='suggestions_review').slice(-2).map(c=>c.args.request.action.kind)",
    ),
    ["defer", "reject"],
  );
  await page.waitForURL(/proposal=3/);
  await page
    .getByRole("button", { name: "Torna alla coda", exact: true })
    .click();
  await filter("Rifiutate");
  await row(2).click();
  await detail
    .getByText("Motivo del rifiuto precedente", { exact: true })
    .waitFor();
  await detail.getByText("Ripetizione intenzionale", { exact: true }).waitFor();
  assert.equal(
    await detail.getByRole("button", { name: "Approva", exact: true }).count(),
    0,
  );
  pass(
    "Structured cleanup editing works at 780×620, saving never activates, keyboard defer/previous/reject, mandatory rejection reason persists",
  );
  await page.evaluate("window.review.proposals[0].evidence=[]");
  // No manual refresh: a focus event revalidates the snapshot.
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page
    .getByRole("button", { name: "Torna alla coda", exact: true })
    .click();
  await filter("Approvate e attive");
  await row(1).click();
  await openSection(/^0 riferimenti · 0 sessioni/);
  await detail.getByText(/Le sessioni sorgenti sono state eliminate/).waitFor();
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
