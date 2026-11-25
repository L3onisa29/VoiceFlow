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
const output = resolve(root, "artifacts/confidence");
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
window.findings=[];window.calls=[];window.failRuns=false;window.failDetail=false;window.delayDetail=false;window.rows=[];
window.analysis={settings:{enabled:false,maxSources:20,maxInputBytes:65536,maxRequestsPerDay:2,requestTimeoutSeconds:120,maxSecondsPerDay:300},requestsToday:0,inputBytesToday:0,elapsedSecondsToday:0,reservedSecondsToday:0,pendingSources:31,excludedSessions:0,lastState:null,lastError:null,acceptedCandidates:0,rejectedCandidates:0};
window.scheduler={schedule:{enabled:false,startMinute:150,endMinute:360},installed:false,loaded:false,matchesConfiguration:false,helperAvailable:true,workerRunning:false,recording:false,phase:'idle',leaseUntil:0,nextAttempt:0,retryCount:0,lastError:null};
window.jobs=[{id:1,startedAt:1750000000000,endedAt:1750000000100,reason:'outside-window',batchId:null}];
window.makeRun=(batchId,attemptId,state,accepted,rejected)=>({key:{batchId,attemptId},state,createdAt:1750000000000,startedAt:attemptId===null?null:1750000000100,endedAt:state==='running'?null:1750000001000,elapsedMs:state==='running'?null:900,sourceCount:2,accepted,rejected,error:state==='failed'?'provider-exit':state==='partial'?'candidate-rejected':null,inputBytes:1000});
const entry={id:1,createdAt:1750000000000,text:'voice flow fixture',wordCount:3,durationSeconds:10,language:'it'};
window.proposal={id:7,revision:2,state:'proposed',rejectionReason:null,approvedAt:null,conflicts:[],candidate:{definition:{category:'terminology',preferred:'VoiceFlow',variants:['voice flow'],matching:'exact',context:{before:null,after:null,exceptBefore:[],exceptAfter:[]}},language:'it',groupIds:[],reason:'Grafia del progetto',positiveExamples:[{input:'voice flow',output:'VoiceFlow'}],negativeExamples:[{input:'voice flower',output:'voice flower'}]},evidence:[{sessionId:1,revisionId:null,originalIndex:null,before:'voice flow',after:null,humanConfirmed:false}]};
mockIPC(async(cmd,args)=>{
 window.calls.push({cmd,args});
 if(cmd==='get_state')return {revision:1,phase:'idle',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:0,device:'Fixture',settings:{language:'italian',format:'spoken-commands',cleanup:true},transcript:{confirmed:'',partial:'',audioSeconds:0,inferenceSeconds:0,chunks:0,finished:false}};
 if(cmd==='get_permissions')return {microphone:true,microphoneStatus:'authorized',accessibility:true};
 if(cmd==='dictionary_snapshot')return {terms:[],groups:[],selectedGroupIds:[],version:1,conflicts:[]};
 if(cmd==='history_page')return {entries:[entry],nextCursor:null,stats:{totalWords:3,totalSessions:1,totalSeconds:10,wordsPerMinute:18}};
 if(cmd==='history_detail')return {entry,originalAsr:[entry.text],producedText:entry.text,processing:null,editVersion:0,currentRevision:null,revisions:[]};
 if(cmd==='analysis_configure'){window.analysis.settings=args.settings;return structuredClone(window.analysis);}
 if(cmd==='analysis_status')return structuredClone(window.analysis);
 if(cmd==='scheduler_status')return structuredClone(window.scheduler);
 if(cmd==='scheduler_invocations')return structuredClone(window.jobs);
 if(cmd==='analysis_excluded')return false;
 if(cmd==='suggestions_snapshot')return {proposals:[window.proposal],version:0,dictionaryVersion:1,groups:[],publications:[]};
 if(cmd==='suggestions_review'){
  const request=args.request;
  if(request.action.kind!=='edit')throw new Error('Unexpected review action');
  window.proposal.candidate=request.action.candidate;window.proposal.revision++;
  window.proposal.confidence=null;window.proposal.analysisOutcome=null;
  return {proposals:[structuredClone(window.proposal)],version:0,dictionaryVersion:1,groups:[],publications:[]};
 }
 if(cmd==='analysis_runs'){
  if(window.failRuns)throw new Error('fixture list unavailable');
  return {entries:structuredClone(args.before?[window.makeRun(1,1,'failed',0,0)]:window.rows),nextCursor:args.before||!window.rows.length?null:{batchId:2,attemptId:2}};
 }
 if(cmd==='analysis_run_detail'){
  if(window.failDetail)throw new Error('Dettaglio temporaneamente non disponibile');
  const run=structuredClone(window.rows.find(r=>r.key.attemptId===args.key.attemptId)||window.makeRun(1,1,'failed',0,0));
  if(window.delayDetail&&args.key.attemptId===2)await new Promise(resolve=>setTimeout(resolve,700));
  return {run,attempts:structuredClone(window.rows.filter(r=>r.key.batchId===run.key.batchId)),findings:structuredClone(window.findings),sources:[{sessionId:1,revisionId:null,excluded:false},{sessionId:2,revisionId:null,excluded:true}],proposals:run.accepted?[{proposalId:7,relation:'created',revision:2,currentState:'proposed',currentRevision:2}]:[]};
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
  await page.goto("https://voiceflow.test/#/settings?section=analysis");
  const policy = page.getByRole("checkbox", {
    name: "Approva automaticamente con confidenza superiore a 90",
    exact: true,
  });
  await policy.waitFor();
  assert.equal(await policy.isChecked(), false);
  // Enabling applies immediately after an explicit confirmation.
  await policy.click();
  await page
    .getByRole("alertdialog", { name: "Approvare automaticamente le regole?" })
    .getByRole("button", { name: "Attiva approvazione", exact: true })
    .click();
  await page.waitForFunction(
    () => window.analysis.settings.autoApproveHighConfidence === true,
  );
  const configured = await page.evaluate(() =>
    window.calls.filter((c) => c.cmd === "analysis_configure").at(-1),
  );
  assert.equal(configured.args.settings.autoApproveHighConfidence, true);
  pass(
    "Legacy settings default off; explicit >90 toggle persists through configure",
  );
  await page.evaluate(() => {
    window.rows = [window.makeRun(1, 1, "running", 0, 0)];
    window.scheduler.workerRunning = true;
  });
  await page
    .getByRole("link", { name: "Torna a VoiceFlow", exact: true })
    .click();
  await page.getByRole("link", { name: "Revisione", exact: true }).click();
  await page.getByRole("tab", { name: "Esecuzioni", exact: true }).click();
  await page.getByRole("link", { name: /Tentativo #1$/ }).click();
  const panel = page.locator('[aria-label="Dettaglio analisi"]');
  await panel.getByText("In corso", { exact: true }).waitFor();
  await page.evaluate(() => {
    window.rows[0] = window.makeRun(1, 1, "completed", 8, 0);
    window.scheduler.workerRunning = false;
    window.findings = [0, 49, 50, 74, 75, 90, 91, 100].map((score, i) => ({
      id: i + 1,
      name: "Rilievo " + score,
      text: "voice flow",
      resultText: score === 100 ? "voice flow" : "VoiceFlow",
      reason: "Grafia del progetto",
      action: score === 100 ? "keep" : "replace",
      confidence: score,
      outcome:
        score === 100
          ? "no-change"
          : score > 90
            ? "auto-approved"
            : "manual-confidence",
      publication: score === 91 ? 1 : null,
      proposalId: score === 100 ? null : 7,
      evidence: [{ sessionId: 1, revisionId: null, originalIndex: null }],
    }));
  });
  await panel
    .getByText("Rilievo 100", { exact: true })
    .waitFor({ timeout: 7000 });
  for (const score of [0, 49, 50, 74, 75, 90, 91, 100]) {
    await panel
      .locator(`[aria-label="Rilievo: Rilievo ${score}"]`)
      .getByText(`Confidenza ${score}/100`, { exact: false })
      .waitFor();
  }
  await panel
    .getByText("Testo mantenuto · nessuna regola pubblicata", { exact: true })
    .waitFor();
  await panel.getByText("Approvata automaticamente", { exact: true }).waitFor();
  await panel
    .getByText("La confidenza è una stima del provider", { exact: false })
    .waitFor();
  await panel
    .locator('[aria-label="Rilievo: Rilievo 91"]')
    .screenshot({ path: resolve(output, "finding-91.png") });
  await page.setViewportSize({ width: 780, height: 620 });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
    false,
  );
  await panel
    .locator('[aria-label="Rilievo: Rilievo 90"]')
    .screenshot({ path: resolve(output, "finding-90-minimum.png") });
  await page.setViewportSize({ width: 1060, height: 760 });
  await page.screenshot({
    path: resolve(output, "findings.png"),
    fullPage: true,
  });
  pass(
    "Production analysis route refreshes completed run with exact confidence scores, source text, action, result, estimate meaning and distinct outcomes",
  );
  await page.evaluate(() => {
    window.proposal.confidence = 91;
    window.proposal.analysisOutcome = "auto-approved";
  });
  await panel
    .getByRole("link", { name: "Apri proposta #7", exact: true })
    .first()
    .click();
  const proposal = page.locator('[data-proposal-detail="7"]');
  await proposal.getByText("Confidenza 91/100", { exact: false }).waitFor();
  // The queue row carries the bare score; the scale appears once, in the detail.
  assert.equal(
    await page.getByText("Confidenza 91/100", { exact: false }).count(),
    1,
  );
  await page
    .locator('[data-proposal-id="7"]')
    .getByText("Confidenza 91", { exact: true })
    .waitFor();
  await proposal.getByText("Ultimo esito analisi", { exact: true }).waitFor();
  await proposal
    .getByText("Approvata automaticamente", { exact: true })
    .waitFor();
  await page.screenshot({
    path: resolve(output, "suggestion-score.png"),
    fullPage: true,
  });
  await page.evaluate(() => {
    delete window.proposal.confidence;
    delete window.proposal.analysisOutcome;
  });
  // Suggestions refresh on focus and events; there is no manual refresh.
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.getByText("Confidenza sconosciuta", { exact: false }).waitFor();
  pass(
    "Linked suggestion shows latest score/outcome; old proposal remains unknown/manual",
  );
  await page.evaluate(() => {
    window.proposal.confidence = 90;
    window.proposal.analysisOutcome = "manual-confidence";
  });
  // Suggestions refresh on focus and events; there is no manual refresh.
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.getByText("Confidenza 90/100", { exact: false }).waitFor();
  await page.getByRole("button", { name: "Modifica", exact: true }).click();
  const edit = page.getByRole("dialog");
  await edit.locator("#preferred").fill("VoiceFLOW");
  await edit.locator("#positiveExamples-0-output").fill("VoiceFLOW");
  await edit
    .getByRole("button", { name: "Salva proposta", exact: true })
    .click();
  await edit.waitFor({ state: "hidden" });
  await page.getByRole("heading", { name: "VoiceFLOW", exact: true }).waitFor();
  await page.getByText("Confidenza sconosciuta", { exact: false }).waitFor();
  assert.equal(
    await page.getByText("Confidenza 90/100", { exact: false }).count(),
    0,
  );
  assert.equal(
    await page.getByText("Ultimo esito analisi", { exact: true }).count(),
    0,
  );
  // The backend regression supplies a fresh score only when the edited candidate
  // matches the new finding. The UI must consume that refreshed snapshot.
  await page.evaluate(() => {
    window.proposal.confidence = 99;
    window.proposal.analysisOutcome = "blocked-human";
  });
  // Suggestions refresh on focus and events; there is no manual refresh.
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.getByRole("heading", { name: "VoiceFLOW", exact: true }).waitFor();
  await page.getByText("Confidenza 99/100", { exact: false }).waitFor();
  await page.getByRole("tab", { name: "Esecuzioni", exact: true }).click();
  await page.getByRole("link", { name: /Tentativo #1$/ }).click();
  await panel
    .locator('[aria-label="Rilievo: Rilievo 90"]')
    .getByText("Confidenza 90/100", { exact: false })
    .waitFor();
  await panel
    .locator('[aria-label="Rilievo: Rilievo 90"]')
    .getByText("VoiceFlow", { exact: true })
    .waitFor();
  pass(
    "Manual edit clears current score/outcome; re-ingestion refreshes score while the original action and score stay in history",
  );
  await page.setViewportSize({ width: 780, height: 620 });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
    false,
  );
  assert.deepEqual(errors, []);
  assert.equal(
    await page.evaluate(() =>
      window.calls.some(
        (c) =>
          c.cmd === "start_capture" ||
          (c.cmd === "suggestions_review" &&
            c.args.request.action.kind !== "edit"),
      ),
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
