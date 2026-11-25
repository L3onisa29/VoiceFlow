// Built UI, intercepted assets + Tauri fixtures. No listener, provider, or audio.
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
const modulePath = process.env.PLAYWRIGHT_MODULE;
const { chromium } = await import(
  modulePath ? pathToFileURL(modulePath).href : "playwright"
);
const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "artifacts/analysis-runs");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1060, height: 760 },
  reducedMotion: "reduce",
});
await context.route("https://voiceflow.test/**", async (route) => {
  const path = new URL(route.request().url()).pathname;
  const file = path === "/" ? "index.html" : path.slice(1);
  await route.fulfill({
    body: await readFile(resolve(root, "apps/desktop/dist", file)),
    contentType: file.endsWith(".js")
      ? "text/javascript"
      : file.endsWith(".css")
        ? "text/css"
        : file.endsWith(".woff2")
          ? "font/woff2"
          : "text/html",
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
window.calls=[];window.failRuns=false;window.failDetail=false;window.delayDetail=false;window.revoked=false;window.deleted=false;window.rows=[];
window.analysis={settings:{enabled:false,maxSources:20,maxInputBytes:65536,maxRequestsPerDay:2,requestTimeoutSeconds:120,maxSecondsPerDay:300},requestsToday:0,inputBytesToday:0,elapsedSecondsToday:0,reservedSecondsToday:0,pendingSources:31,excludedSessions:0,lastState:null,lastError:null,acceptedCandidates:0,rejectedCandidates:0};
window.scheduler={schedule:{enabled:false,startMinute:150,endMinute:360},installed:false,loaded:false,matchesConfiguration:false,helperAvailable:true,workerRunning:false,recording:false,phase:'idle',leaseUntil:0,nextAttempt:0,retryCount:0,lastError:null};
window.jobs=[{id:1,startedAt:1750000000000,endedAt:1750000000100,reason:'outside-window',batchId:null}];
window.makeRun=(batchId,attemptId,state,accepted,rejected)=>({key:{batchId,attemptId},state,createdAt:1750000000000,startedAt:attemptId===null?null:1750000000100,endedAt:state==='running'?null:1750000001000,elapsedMs:state==='running'?null:900,sourceCount:3,accepted,rejected,error:state==='failed'?'provider-exit':state==='partial'?'candidate-rejected':null,inputBytes:1000});
const entry={id:1,createdAt:1750000000000,text:'voice flow fixture',wordCount:3,durationSeconds:10,language:'it'};
const proposal={id:7,revision:2,state:'proposed',rejectionReason:null,approvedAt:null,conflicts:[],candidate:{definition:{category:'terminology',preferred:'VoiceFlow',variants:['voice flow'],matching:'exact',context:{before:null,after:null,exceptBefore:[],exceptAfter:[]}},language:'it',groupIds:[],reason:'Grafia del progetto',positiveExamples:[{input:'voice flow',output:'VoiceFlow'}],negativeExamples:[{input:'voice flower',output:'voice flower'}]},evidence:[{sessionId:1,revisionId:null,originalIndex:null,before:'voice flow',after:null,humanConfirmed:false}]};
mockIPC(async(cmd,args)=>{
 window.calls.push({cmd,args});
 if(cmd==='get_state')return {revision:1,phase:'idle',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:0,device:'Fixture',settings:{language:'italian',format:'spoken-commands',cleanup:true},transcript:{confirmed:'',partial:'',audioSeconds:0,inferenceSeconds:0,chunks:0,finished:false}};
 if(cmd==='get_permissions')return {microphone:true,microphoneStatus:'authorized',accessibility:true};
 if(cmd==='dictionary_snapshot')return {terms:[],groups:[],selectedGroupIds:[],version:1,conflicts:[]};
 if(cmd==='history_page')return {entries:[entry],nextCursor:null,stats:{totalWords:3,totalSessions:1,totalSeconds:10,wordsPerMinute:18}};
 if(cmd==='history_detail'){if(window.deleted)throw new Error('Fonte eliminata');return {entry,originalAsr:[entry.text],producedText:entry.text,processing:null,editVersion:1,currentRevision:8,revisions:[{id:8,createdAt:1750000000100,beforeText:'voice flow fixture',text:'VoiceFlow fixture',reverted:false,category:'recognition'}]};}
 if(cmd==='analysis_status')return structuredClone(window.analysis);
 if(cmd==='scheduler_status')return structuredClone(window.scheduler);
 if(cmd==='scheduler_invocations')return structuredClone(window.jobs);
 if(cmd==='scheduler_run_now') {
  await new Promise(resolve=>setTimeout(resolve,200));
  if(window.failManual)throw new Error('Helper temporaneamente non disponibile');
  const job={id:window.jobs[0].id+1,origin:'manual',startedAt:Date.now(),endedAt:null,reason:'requested',batchId:null,attemptId:null};
  window.jobs.unshift(job);return structuredClone(job);
 }
 if(cmd==='analysis_excluded')return window.revoked;
 if(cmd==='suggestions_snapshot')return {proposals:[proposal],version:0,dictionaryVersion:1,groups:[],publications:[{version:2,previousVersion:1,action:'approve',createdAt:1750000000100,proposalIds:[7]}]};
 if(cmd==='analysis_runs'){
  if(window.failRuns)throw new Error('fixture list unavailable');
  const f=args.filters||{};
  const rows=window.rows.filter(r=>(!f.state||r.state===f.state)&&(!f.origin||(r.origin||'unknown')===f.origin)&&(!args.before||r.key.batchId<args.before.batchId||(r.key.batchId===args.before.batchId&&(r.key.attemptId||0)<(args.before.attemptId||0))));
  return {entries:structuredClone(rows.slice(0,args.limit)),nextCursor:rows.length>args.limit?structuredClone(rows[args.limit-1].key):null};
 }
 if(cmd==='analysis_run_detail'){
  if(window.failDetail)throw new Error('Dettaglio temporaneamente non disponibile');
  const run=structuredClone(window.rows.find(r=>r.key.batchId===args.key.batchId&&r.key.attemptId===args.key.attemptId));
  if(!run)throw new Error('Analisi non disponibile');
  const data={run,attempts:structuredClone(window.rows.filter(r=>r.key.batchId===run.key.batchId&&r.key.attemptId!==null)),sources:window.deleted?[]:[{sessionId:1,revisionId:null,excluded:window.revoked},{sessionId:1,revisionId:8,excluded:window.revoked},{sessionId:2,revisionId:null,excluded:true}],proposals:run.accepted?[{proposalId:7,relation:'created',revision:2,currentState:'disabled',currentRevision:3}]:[],findings:run.accepted&&!window.revoked&&!window.deleted?[{id:1,name:'Grafia del progetto',text:'voice flow',resultText:'VoiceFlow',reason:'Usa il nome del progetto',action:'replace',confidence:96,outcome:'auto-approved',publication:2,proposalId:7,evidence:[{sessionId:1,revisionId:null,originalIndex:0}]}]:[],spellings:run.accepted&&!window.revoked&&!window.deleted?[{spelling:'tes flight',intended:'TestFlight',sessionIds:[1],confidence:95,realWord:false,outcome:'added'},{spelling:'tasche',intended:'task',sessionIds:[1,2],confidence:96,realWord:true,outcome:'waiting-sessions'}]:[],rejections:run.accepted&&!window.revoked&&!window.deleted?[{kind:'misspelling',position:3,code:'excerpt-not-in-source',sources:[{sessionId:1,revisionId:null}]}]:[]};
  if(window.delayDetail&&args.key.attemptId===2)await new Promise(resolve=>setTimeout(resolve,700));
  return data;
 }
 return null;
},{shouldMockEvents:true});`,
});
const page = await context.newPage();
page.setDefaultTimeout(10000);
const errors: string[] = [];
page.on("pageerror", (e) => errors.push(String(e)));
const checks: string[] = [];
const pass = (name: string) => {
  checks.push(name);
  console.log("PASS", name);
};
const detail = page.locator('[aria-label="Dettaglio analisi"]');
const focusRefresh = () =>
  page.evaluate(() => window.dispatchEvent(new Event("focus")));
const go = async (path: string) => {
  await page.evaluate((path) => {
    window.location.hash = path;
  }, path);
};
const openAttempt = (id: number) =>
  page.getByRole("link", { name: new RegExp(`Tentativo #${id}$`) }).click();
// Run details return to the list through the breadcrumb, with filters kept.
const backToRuns = () =>
  page
    .getByRole("navigation", { name: "Percorso" })
    .getByRole("link", { name: "Esecuzioni", exact: true })
    .click();
const runButton = page.getByRole("button", {
  name: "Esegui analisi ora",
  exact: true,
});
const confirm = page.getByRole("dialog");
const toasts = page.getByRole("region", { name: /Notifiche/ });
const toast = (text: string) =>
  toasts.locator("[data-sonner-toast]", { hasText: text });
try {
  await page.goto("https://voiceflow.test/#/review/runs");
  await page.getByText("Nessuna analisi registrata", { exact: true }).waitFor();
  assert.equal(
    await page.getByRole("button", { name: "Aggiorna", exact: true }).count(),
    0,
  );
  // The header action states its blocker inside the confirmation.
  await runButton.click();
  await confirm
    .getByText("Attiva il consenso OpenAI nelle Impostazioni.")
    .waitFor();
  assert.equal(
    await confirm
      .getByRole("button", { name: "Avvia analisi", exact: true })
      .isDisabled(),
    true,
  );
  await page.keyboard.press("Escape");
  await confirm.waitFor({ state: "hidden" });
  assert.equal(
    await page.getByText("Pianificazione automatica", { exact: true }).count(),
    0,
  );
  await page.getByText("Diagnostica degli avvii", { exact: true }).click();
  await page.getByText("Fuori dalla fascia oraria", { exact: true }).waitFor();
  pass(
    "Compact empty list preserves manual blockers and secondary diagnostics without scheduler panel",
  );
  await page.evaluate(() => {
    window.rows = [
      { ...window.makeRun(50, null, "prepared", null, null), origin: null },
      ...Array.from({ length: 26 }, (_, i) => ({
        ...window.makeRun(40 - i, 40 - i, "completed", 0, 0),
        origin: "automatic",
      })),
      {
        ...window.makeRun(2, 3, "completed", 0, 0),
        origin: "manual",
        model: "gpt-6-sol",
        reasoningEffort: "high",
      },
      {
        ...window.makeRun(2, 2, "partial", 1, 1),
        origin: "manual",
        model: "gpt-6-sol",
        reasoningEffort: "high",
      },
      { ...window.makeRun(1, 1, "failed", 0, 0), origin: null },
    ];
  });
  // No manual refresh: focus and history events revalidate the list.
  await focusRefresh();
  await page.getByRole("link", { name: /Lotto #40/ }).waitFor();
  assert.equal(
    await page
      .getByRole("navigation", { name: "Pagine delle esecuzioni" })
      .getByRole("link", { name: "Precedenti", exact: true })
      .count(),
    1,
  );
  assert.equal(
    await page.getByRole("link", { name: /Tentativo #2$/ }).count(),
    0,
  );
  await page.getByRole("combobox", { name: "Filtra per origine" }).click();
  await page.getByRole("option", { name: "Manuale", exact: true }).click();
  await openAttempt(2);
  await detail.getByText("Grafia del progetto", { exact: true }).waitFor();
  const spellings = detail.getByLabel("Grafie osservate");
  await spellings.getByText("«tes flight» → TestFlight").waitFor();
  await spellings
    .getByText("Aggiunta al dizionario", { exact: true })
    .waitFor();
  await spellings
    .getByText("In attesa · parola esistente, servono 5 sessioni concordi")
    .waitFor();
  await detail
    .getByLabel("Candidati scartati")
    .getByText("Grafia 3", { exact: true })
    .waitFor();
  assert.match(page.url(), /review\/runs\/2\/attempts\/2/);
  assert.match(page.url(), /origin=manual/);
  await detail
    .getByText("Ora: Approvata · disattivata, revisione 3.")
    .waitFor();
  await detail.locator("del").waitFor();
  await detail.locator("ins").waitFor();
  await detail
    .getByRole("button", { name: "Trascrizione #2 · Esclusa", exact: true })
    .isDisabled()
    .then((value) => assert.equal(value, true));
  assert.equal(
    await detail
      .getByRole("navigation", { name: "Tentativi dello stesso lotto" })
      .getByRole("link")
      .count(),
    2,
  );
  await detail.getByText("Dettagli tecnici", { exact: true }).click();
  await detail.getByText("gpt-6-sol", { exact: true }).waitFor();
  await detail.getByText("high", { exact: true }).waitFor();
  await page.locator("#page-content").evaluate((el) => el.scrollTo(0, 0));
  await page.screenshot({
    path: resolve(output, "analysis-detail-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 780, height: 620 });
  await page.screenshot({
    path: resolve(output, "analysis-detail-minimum.png"),
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
    false,
  );
  pass(
    "Global origin filtering finds older archive rows; exact sibling attempts, historical/current proposal distinction, before/after and recorded metadata",
  );
  await detail
    .getByRole("button", { name: "Correzione #1 · Revisione #8", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByText("VoiceFlow fixture", { exact: true })
    .waitFor();
  await page
    .getByRole("dialog")
    .getByRole("heading", { name: "Prima della correzione" })
    .waitFor();
  assert.equal(await page.getByRole("dialog").getByRole("textbox").count(), 0);
  await page.keyboard.press("Escape");
  await detail
    .getByRole("button", { name: "Trascrizione #1", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("heading", { name: "Originale ASR" })
    .waitFor();
  await page.evaluate(() => {
    window.revoked = true;
    window.dispatchEvent(new Event("focus"));
  });
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  assert.equal(
    await detail.getByText("voice flow", { exact: true }).count(),
    0,
  );
  await page.evaluate(() => {
    window.revoked = false;
    window.dispatchEvent(new Event("focus"));
  });
  // A prior inspected source may become visible after revalidation; close it before testing deletion.
  if (await page.getByRole("dialog").count())
    await page.keyboard.press("Escape");
  await detail
    .getByRole("button", { name: "Trascrizione #1", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("heading", { name: "Originale ASR" })
    .waitFor();
  await page.evaluate(() => {
    window.deleted = true;
    window.dispatchEvent(new Event("focus"));
  });
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await detail.getByText(/0 riferimenti disponibili/).waitFor();
  await page.evaluate(() => {
    window.deleted = false;
    window.dispatchEvent(new Event("focus"));
  });
  if (await page.getByRole("dialog").count())
    await page.keyboard.press("Escape");
  pass(
    "Read-only source view shows exact correction; privacy exclusion and deletion remove open source text and findings",
  );
  await backToRuns();
  await openAttempt(3);
  await detail.getByText("Analisi completata senza suggerimenti.").waitFor();
  await page.goBack();
  await page
    .getByRole("combobox", { name: "Filtra per origine" })
    .getByText("Manuale", { exact: true })
    .waitFor();
  assert.match(page.url(), /selectedAttempt=3/);
  pass("Back navigation retains filters and selected row in URL");
  await go("/review/runs?beforeBatch=22&beforeAttempt=22&origin=automatic");
  await page.getByRole("link", { name: /Tentativo #21$/ }).waitFor();
  await openAttempt(21);
  await detail.getByText("Analisi completata senza suggerimenti.").waitFor();
  // Persist synthetic archive across direct reload, independently of page JS memory.
  const savedRows = await page.evaluate(() => window.rows);
  await context.addInitScript((rows) => {
    window.rows = rows;
  }, savedRows);
  await page.reload();
  await detail.getByText("Analisi completata senza suggerimenti.").waitFor();
  assert.match(page.url(), /beforeBatch=22/);
  await backToRuns();
  await page.getByRole("link", { name: /Tentativo #21$/ }).waitFor();
  await page.reload();
  await page.getByRole("link", { name: /Tentativo #21$/ }).waitFor();
  assert.equal(
    await page.getByRole("link", { name: /Tentativo #40$/ }).count(),
    0,
  );
  await page.getByRole("combobox", { name: "Filtra per esito" }).click();
  await page.getByRole("option", { name: "Non riuscita", exact: true }).click();
  assert.doesNotMatch(page.url(), /beforeBatch/);
  await page
    .getByText("Nessuna analisi in questa vista", { exact: true })
    .waitFor();
  await page.getByRole("combobox", { name: "Filtra per origine" }).click();
  await page
    .getByRole("option", { name: "Non disponibile", exact: true })
    .click();
  await openAttempt(1);
  await detail
    .getByText("Codex CLI non ha completato la richiesta.", { exact: false })
    .waitFor();
  await detail.getByText("Dettagli tecnici", { exact: true }).click();
  assert.equal(await detail.getByText("gpt-6-sol", { exact: true }).count(), 0);
  pass(
    "Deep hash URLs and reload restore older page/filters; filter changes reset cursor; unknown historical metadata remains unknown",
  );
  await go("/review/runs/50/prepared");
  await detail
    .getByText("Fonti preparate. Nessuna richiesta inviata per questo lotto.")
    .waitFor();
  await go("/review/runs/0/attempts/1");
  await page
    .getByText("Il collegamento contiene un identificatore non valido.")
    .waitFor();
  await go("/review/runs/999/attempts/1");
  await page.getByRole("button", { name: "Riprova dettaglio" }).waitFor();
  await go("/review/runs/2/attempts/2");
  await detail.getByText("Grafia del progetto", { exact: true }).waitFor();
  await page.evaluate(() => {
    window.failDetail = true;
    window.dispatchEvent(new Event("focus"));
  });
  await page.getByRole("button", { name: "Riprova dettaglio" }).waitFor();
  await page.evaluate(() => {
    window.failDetail = false;
  });
  await page.getByRole("button", { name: "Riprova dettaglio" }).click();
  await detail.getByText("Grafia del progetto", { exact: true }).waitFor();
  await page.evaluate(() => {
    window.delayDetail = true;
    window.dispatchEvent(new Event("focus"));
  });
  await detail
    .getByRole("link", { name: "#3 · Completata", exact: true })
    .click();
  await detail.getByText("Analisi completata senza suggerimenti.").waitFor();
  await page.waitForTimeout(850);
  assert.equal(
    await detail.getByText("Grafia del progetto", { exact: true }).count(),
    0,
  );
  assert.match(page.url(), /attempts\/3/);
  await page.evaluate(() => {
    const row = window.rows.find((r) => r.key.attemptId === 3);
    row.state = "running";
    row.endedAt = null;
    window.dispatchEvent(new Event("focus"));
  });
  await detail.getByText("In corso", { exact: true }).waitFor();
  await page.evaluate(() => {
    const row = window.rows.find((r) => r.key.attemptId === 3);
    row.state = "completed";
    row.endedAt = 1750000001000;
  });
  await detail
    .getByText("Analisi completata senza suggerimenti.")
    .waitFor({ timeout: 7000 });
  pass(
    "Prepared route, invalid IDs, missing and failed detail recovery, delayed old route response and bounded running poll",
  );
  await go("/review/runs/2/attempts/2");
  await detail.getByRole("link", { name: "Pubblicazione storica #2" }).click();
  await page.locator('[data-publication-version="2"]').waitFor();
  assert.match(page.url(), /publication=2/);
  await page.goBack();
  await detail.getByRole("link", { name: "Apri proposta #7" }).click();
  // Narrow layout: the linked proposal opens alone, with a way back to the queue.
  await page.locator('[data-proposal-detail="7"]').waitFor();
  assert.match(page.url(), /proposal=7/);
  await page
    .getByRole("button", { name: "Torna alla coda", exact: true })
    .waitFor();
  pass(
    "Finding links resolve exact suggestion and historical publication without changing approval logic",
  );
  await go("/review/runs");
  await page.evaluate(() => {
    window.analysis.settings.enabled = true;
    window.analysis.settings.autoApproveHighConfidence = true;
    window.scheduler.schedule.enabled = false;
    window.dispatchEvent(new Event("focus"));
  });
  await runButton.click();
  await confirm
    .getByText("Approvazione automatica attiva:", { exact: false })
    .waitFor();
  // The effect of auto-approval is stated once, in the confirmation only.
  assert.equal(
    await page
      .getByText(/Approvazione automatica (attiva|disattivata):/)
      .count(),
    1,
  );
  await confirm
    .getByRole("button", { name: "Avvia analisi", exact: true })
    .evaluate((button) => {
      (button as HTMLButtonElement).click();
      (button as HTMLButtonElement).click();
    });
  const manual = page.getByRole("region", { name: "Avvio manuale" });
  await manual
    .getByText("Richiesta di avvio ricevuta", { exact: true })
    .waitFor();
  await confirm.waitFor({ state: "hidden" });
  assert.equal(
    await page.evaluate(
      () => window.calls.filter((c) => c.cmd === "scheduler_run_now").length,
    ),
    1,
  );
  assert.equal(await runButton.isDisabled(), true);
  await page.evaluate(() => {
    Object.assign(window.jobs[0], {
      reason: "running",
      batchId: 2,
      attemptId: 3,
    });
    window.scheduler.workerRunning = true;
    window.dispatchEvent(new Event("focus"));
  });
  await manual.getByText("Analisi in esecuzione", { exact: true }).waitFor();
  await manual.getByRole("button", { name: "Apri esito", exact: true }).click();
  assert.match(page.url(), /review\/runs\/2\/attempts\/3/);
  await backToRuns();
  await page.evaluate(() => {
    Object.assign(window.jobs[0], { reason: "completed", endedAt: Date.now() });
    window.scheduler.workerRunning = false;
    window.dispatchEvent(new Event("focus"));
  });
  await toast("Analisi conclusa")
    .getByRole("button", { name: "Apri esito", exact: true })
    .waitFor();
  await manual.waitFor({ state: "detached" });
  assert.equal(await runButton.isDisabled(), false);
  for (const [reason, title, description] of [
    [
      "no-work",
      "Nessuna fonte da analizzare",
      "Nessuna fonte preparabile, anche per esclusioni o limite di dimensione",
    ],
    [
      "backoff",
      "Analisi non avviata",
      "In attesa dopo un tentativo non riuscito (5 minuti, al massimo 3 al giorno)",
    ],
    [
      "consent-unavailable",
      "Analisi non avviata",
      "Consenso OpenAI assente o non leggibile",
    ],
    [
      "helper-unavailable",
      "Analisi non avviata",
      "Helper non disponibile. Apri la build completa di VoiceFlow.",
    ],
  ]) {
    await runButton.click();
    await confirm
      .getByRole("button", { name: "Avvia analisi", exact: true })
      .click();
    await manual
      .getByText("Richiesta di avvio ricevuta", { exact: true })
      .waitFor();
    await page.evaluate((reason) => {
      window.jobs[0].reason = reason;
      window.jobs[0].endedAt = Date.now();
      window.dispatchEvent(new Event("focus"));
    }, reason);
    await manual.waitFor({ state: "detached" });
    const outcome = toast(description);
    await outcome.getByText(title, { exact: true }).waitFor();
    assert.equal(
      await outcome.getByRole("button", { name: /Apri esito/ }).count(),
      0,
    );
    // Never the raw reason code.
    assert.equal(await toasts.getByText(reason, { exact: true }).count(), 0);
  }
  await page.evaluate(() => {
    window.failManual = true;
  });
  await runButton.click();
  await confirm
    .getByRole("button", { name: "Avvia analisi", exact: true })
    .click();
  await toast("Helper temporaneamente non disponibile").waitFor();
  assert.equal(await manual.count(), 0);
  await page.keyboard.press("Escape");
  await confirm.waitFor({ state: "hidden" });
  await page.evaluate(() => {
    window.failManual = false;
    window.analysis.settings.autoApproveHighConfidence = false;
    window.dispatchEvent(new Event("focus"));
  });
  await runButton.click();
  await confirm
    .getByText("Approvazione automatica disattivata:", { exact: false })
    .waitFor();
  await page.keyboard.press("Escape");
  await confirm.waitFor({ state: "hidden" });
  pass(
    "Manual header action preserves double-click guard, states auto-approval once, request/start alert, toast outcomes with exact route link, no-work/backoff/consent/helper states without raw codes, and retry",
  );
  await page.evaluate(() => {
    window.failRuns = true;
    window.dispatchEvent(new Event("focus"));
  });
  await page.getByText("Impossibile caricare le esecuzioni.").waitFor();
  await page.evaluate(() => {
    window.failRuns = false;
  });
  await page.getByRole("button", { name: "Riprova", exact: true }).click();
  await page.getByRole("link", { name: /Tentativo #40$/ }).waitFor();
  await page.locator("#page-content").evaluate((el) => el.scrollTo(0, 0));
  await page.screenshot({
    path: resolve(output, "analysis-runs-minimum.png"),
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
    false,
  );
  await page.setViewportSize({ width: 1060, height: 760 });
  await page.screenshot({
    path: resolve(output, "analysis-runs.png"),
    fullPage: true,
  });
  assert.deepEqual(errors, []);
  assert.equal(
    await page.evaluate(() =>
      window.calls.some((c) => /configure|review|start_capture/.test(c.cmd)),
    ),
    false,
  );
  pass(
    "List read error recovery, both viewport layouts, no unexpected mutations or audio",
  );
  await writeFile(
    resolve(output, "ui-checks.json"),
    JSON.stringify({ checks, errors }, null, 2),
  );
} catch (error) {
  console.error(await page.locator("body").innerText());
  throw error;
} finally {
  await browser.close();
}
