// Built UI, intercepted assets + Tauri fixtures. No listener, provider, or audio.
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
const modulePath = process.env.PLAYWRIGHT_MODULE;
if (!modulePath) throw new Error("Set PLAYWRIGHT_MODULE");
const { chromium } = await import(pathToFileURL(modulePath).href);
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
window.calls=[];window.failRuns=false;window.failDetail=false;window.delayDetail=false;window.rows=[];
window.analysis={settings:{enabled:false,maxSources:20,maxInputBytes:65536,maxRequestsPerDay:2,requestTimeoutSeconds:120,maxSecondsPerDay:300},requestsToday:0,inputBytesToday:0,elapsedSecondsToday:0,reservedSecondsToday:0,pendingSources:31,excludedSessions:0,lastState:null,lastError:null,acceptedCandidates:0,rejectedCandidates:0};
window.scheduler={schedule:{enabled:false,startMinute:150,endMinute:360},installed:false,loaded:false,matchesConfiguration:false,helperAvailable:true,workerRunning:false,recording:false,phase:'idle',leaseUntil:0,nextAttempt:0,retryCount:0,lastError:null};
window.jobs=[{id:1,startedAt:1750000000000,endedAt:1750000000100,reason:'outside-window',batchId:null}];
window.makeRun=(batchId,attemptId,state,accepted,rejected)=>({key:{batchId,attemptId},state,createdAt:1750000000000,startedAt:attemptId===null?null:1750000000100,endedAt:state==='running'?null:1750000001000,elapsedMs:state==='running'?null:900,sourceCount:2,accepted,rejected,error:state==='failed'?'provider-exit':state==='partial'?'candidate-rejected':null,inputBytes:1000});
const entry={id:1,createdAt:1750000000000,text:'voice flow fixture',wordCount:3,durationSeconds:10,language:'it'};
const proposal={id:7,revision:2,state:'proposed',rejectionReason:null,approvedAt:null,conflicts:[],candidate:{definition:{category:'terminology',preferred:'VoiceFlow',variants:['voice flow'],matching:'exact',context:{before:null,after:null,exceptBefore:[],exceptAfter:[]}},language:'it',groupIds:[],reason:'Grafia del progetto',positiveExamples:[{input:'voice flow',output:'VoiceFlow'}],negativeExamples:[{input:'voice flower',output:'voice flower'}]},evidence:[{sessionId:1,revisionId:null,originalIndex:null,before:'voice flow',after:null,humanConfirmed:false}]};
mockIPC(async(cmd,args)=>{
 window.calls.push({cmd,args});
 if(cmd==='get_state')return {revision:1,phase:'idle',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:0,device:'Fixture',settings:{language:'italian',format:'spoken-commands',cleanup:true},transcript:{confirmed:'',partial:'',audioSeconds:0,inferenceSeconds:0,chunks:0,finished:false}};
 if(cmd==='get_permissions')return {microphone:true,microphoneStatus:'authorized',accessibility:true};
 if(cmd==='dictionary_snapshot')return {terms:[],groups:[],selectedGroupIds:[],version:1,conflicts:[]};
 if(cmd==='history_page')return {entries:[entry],nextCursor:null,stats:{totalWords:3,totalSessions:1,totalSeconds:10,wordsPerMinute:18}};
 if(cmd==='history_detail')return {entry,originalAsr:[entry.text],producedText:entry.text,processing:null,editVersion:0,currentRevision:null,revisions:[]};
 if(cmd==='analysis_status')return structuredClone(window.analysis);
 if(cmd==='scheduler_status')return structuredClone(window.scheduler);
 if(cmd==='scheduler_invocations')return structuredClone(window.jobs);
 if(cmd==='analysis_excluded')return false;
 if(cmd==='suggestions_snapshot')return {proposals:[proposal],version:0,dictionaryVersion:1,groups:[],publications:[]};
 if(cmd==='analysis_runs'){
  if(window.failRuns)throw new Error('fixture list unavailable');
  return {entries:structuredClone(args.before?[window.makeRun(1,1,'failed',0,0)]:window.rows),nextCursor:args.before||!window.rows.length?null:{batchId:2,attemptId:2}};
 }
 if(cmd==='analysis_run_detail'){
  if(window.failDetail)throw new Error('Dettaglio temporaneamente non disponibile');
  const run=structuredClone(window.rows.find(r=>r.key.attemptId===args.key.attemptId)||window.makeRun(1,1,'failed',0,0));
  if(window.delayDetail&&args.key.attemptId===2)await new Promise(resolve=>setTimeout(resolve,700));
  return {run,sources:[{sessionId:1,revisionId:null,excluded:false},{sessionId:2,revisionId:null,excluded:true}],proposals:run.accepted?[{proposalId:7,relation:'created',revision:2}]:[]};
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
try {
  await page.goto("https://voiceflow.test/#/analysis");
  await page.getByText("Nessuna analisi registrata", { exact: true }).waitFor();
  await page
    .getByText("Analisi automatica non attiva: consenso OpenAI disattivato.")
    .waitFor();
  await page.getByText("Fuori dalla fascia oraria", { exact: true }).waitFor();
  await page.getByRole("link", { name: "Analisi", exact: true }).waitFor();
  assert.equal(
    await page.evaluate(() =>
      window.calls.some((c) => /configure|review|start_capture/.test(c.cmd)),
    ),
    false,
  );
  pass(
    "Dedicated navigation, empty history, disabled consent, outside-window diagnostics; no activation",
  );
  await page.evaluate(() => {
    window.rows = [
      window.makeRun(3, 3, "completed", 0, 0),
      window.makeRun(2, 2, "partial", 1, 1),
    ];
  });
  await page.getByRole("button", { name: "Aggiorna", exact: true }).click();
  await page
    .getByRole("button", { name: "Dettaglio tentativo #3", exact: true })
    .click();
  // Card is not a region by default; use its accessible label selector.
  const panel = page.locator('[aria-label="Dettaglio analisi"]');
  await panel.getByText("Analisi completata senza suggerimenti.").waitFor();
  pass("Completed analysis with zero suggestions remains inspectable");
  await page
    .getByRole("button", { name: "Dettaglio tentativo #2", exact: true })
    .click();
  await panel
    .getByText("Alcune proposte non hanno superato la validazione.", {
      exact: false,
    })
    .waitFor();
  assert.equal(
    await panel
      .getByRole("button", { name: "Sessione #2 · Esclusa", exact: true })
      .isDisabled(),
    true,
  );
  await panel.getByRole("button", { name: "Sessione #1", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("heading", { name: "Sessione #1", exact: true })
    .waitFor();
  await page
    .getByRole("dialog")
    .getByText("Originale ASR", { exact: false })
    .first()
    .waitFor();
  await page.keyboard.press("Escape");
  await panel
    .getByRole("link", {
      name: "Proposta #7 · creata · revisione 2",
      exact: true,
    })
    .click();
  await page.locator('[data-proposal-id="7"]').waitFor();
  assert.match(page.url(), /proposal=7/);
  pass(
    "Partial results link exact proposal and existing session detail; excluded source cannot open",
  );
  await page.getByRole("link", { name: "Analisi", exact: true }).click();
  await page
    .getByRole("button", { name: "Tentativi precedenti", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Dettaglio tentativo #1", exact: true })
    .click();
  await panel
    .getByText("Codex CLI non ha completato la richiesta.", { exact: false })
    .waitFor();
  await page.getByRole("button", { name: "Più recenti", exact: true }).click();
  pass(
    "Keyset pagination exposes an older failed attempt and its readable error",
  );
  await page.evaluate(() => {
    window.failDetail = true;
  });
  await page
    .getByRole("button", { name: "Dettaglio tentativo #2", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Riprova dettaglio", exact: true })
    .waitFor();
  await page.evaluate(() => {
    window.failDetail = false;
  });
  await page
    .getByRole("button", { name: "Riprova dettaglio", exact: true })
    .click();
  await panel
    .getByRole("link", {
      name: "Proposta #7 · creata · revisione 2",
      exact: true,
    })
    .waitFor();
  await page.evaluate(() => {
    window.delayDetail = true;
  });
  await page
    .getByRole("button", { name: "Dettaglio tentativo #3", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Dettaglio tentativo #2", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Dettaglio tentativo #3", exact: true })
    .click();
  await panel.getByText("Analisi completata senza suggerimenti.").waitFor();
  await page.waitForTimeout(850);
  assert.match(await panel.innerText(), /Tentativo #3/);
  pass(
    "Detail error retries; a delayed older selection cannot replace the current detail",
  );
  await page.evaluate(() => {
    window.delayDetail = false;
    window.rows[0].state = "running";
    window.rows[0].endedAt = null;
    window.scheduler.workerRunning = true;
    window.dispatchEvent(new Event("focus"));
  });
  await panel.getByText("In corso", { exact: true }).waitFor();
  await page.evaluate(() => {
    window.rows[0].state = "completed";
    window.rows[0].endedAt = 1750000001000;
    window.scheduler.workerRunning = false;
  });
  await panel
    .getByText("Analisi completata senza suggerimenti.")
    .waitFor({ timeout: 7000 });
  pass(
    "Focus refresh and bounded polling show a run completing without manual refresh",
  );
  await page.evaluate(() => {
    window.failRuns = true;
    window.dispatchEvent(new Event("focus"));
  });
  await page
    .getByText("Alcuni dati non sono stati aggiornati. Riprova con Aggiorna.")
    .waitFor();
  await page.evaluate(() => {
    window.failRuns = false;
    window.analysis.settings.enabled = true;
    window.scheduler.schedule.enabled = true;
    window.scheduler.installed = true;
    window.scheduler.loaded = true;
    window.scheduler.matchesConfiguration = true;
  });
  await page.getByRole("button", { name: "Aggiorna", exact: true }).click();
  await page.getByText("Job pronto per la fascia", { exact: false }).waitFor();
  await page.screenshot({
    path: resolve(output, "analysis-runs.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 780, height: 620 });
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
  pass(
    "List error recovers; loaded job remains distinct from successful analysis; minimum desktop width fits",
  );
  assert.deepEqual(errors, []);
  assert.equal(
    await page.evaluate(() =>
      window.calls.some((c) => /configure|review|start_capture/.test(c.cmd)),
    ),
    false,
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
