// Built UI, intercepted assets + Tauri fixtures. No listener, provider, or audio.
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
const modulePath = process.env.PLAYWRIGHT_MODULE;
if (!modulePath) throw new Error("Set PLAYWRIGHT_MODULE");
const { chromium } = await import(pathToFileURL(modulePath).href);
const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "artifacts/scheduler-d2");
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
window.calls=[];window.failAnalysis=false;window.excluded=false;window.failScheduler=false;
window.scheduler={schedule:{enabled:false,startMinute:150,endMinute:360},installed:false,loaded:false,matchesConfiguration:false,helperAvailable:true,workerRunning:false,recording:false,phase:'idle',leaseUntil:0,nextAttempt:0,retryCount:0,lastError:null};
window.analysis={settings:{enabled:false,maxSources:20,maxInputBytes:65536,maxRequestsPerDay:2,requestTimeoutSeconds:120,maxSecondsPerDay:300},requestsToday:0,inputBytesToday:0,elapsedSecondsToday:0,reservedSecondsToday:0,pendingSources:3,excludedSessions:0,lastState:null,lastError:null,acceptedCandidates:0,rejectedCandidates:0};
const entry={id:1,createdAt:1750000000000,text:'voice flow fixture',wordCount:3,durationSeconds:10,language:'it'};
mockIPC(async(cmd,args)=>{
 window.calls.push({cmd,args});
 if(cmd==='get_state')return {revision:1,phase:'idle',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:0,device:'Fixture',settings:{language:'italian',format:'spoken-commands',cleanup:true},transcript:{confirmed:'',partial:'',audioSeconds:0,inferenceSeconds:0,chunks:0,finished:false}};
 if(cmd==='get_permissions')return {microphone:true,microphoneStatus:'authorized',accessibility:true};
 if(cmd==='dictionary_snapshot')return {terms:[],groups:[],selectedGroupIds:[],version:1,conflicts:[]};
 if(cmd==='history_page')return {entries:[entry],nextCursor:null,stats:{totalWords:3,totalSessions:1,totalSeconds:10,wordsPerMinute:18}};
 if(cmd==='history_detail')return {entry,originalAsr:[entry.text],producedText:entry.text,processing:null,editVersion:0,currentRevision:null,revisions:[]};
 if(cmd==='scheduler_status')return structuredClone(window.scheduler);
 if(cmd==='scheduler_configure'){window.scheduler.schedule=structuredClone(args.schedule);window.scheduler.lastError=window.failScheduler?'Installazione launchd non riuscita. Preferenza salvata; usa Ripara pianificazione.':null;if(!window.failScheduler){window.scheduler.installed=args.schedule.enabled;window.scheduler.loaded=args.schedule.enabled;window.scheduler.matchesConfiguration=args.schedule.enabled;}return structuredClone(window.scheduler);}
 if(cmd==='analysis_status')return structuredClone(window.analysis);
 if(cmd==='analysis_configure'){if(window.failAnalysis)throw new Error('Salvataggio analisi non riuscito');window.analysis.settings=structuredClone(args.settings);return structuredClone(window.analysis);}
 if(cmd==='analysis_excluded')return window.excluded;
 if(cmd==='analysis_exclude'){if(window.failAnalysis)throw new Error('Esclusione non salvata');window.excluded=args.excluded;window.analysis.excludedSessions=args.excluded?1:0;return;}
 return null;
},{shouldMockEvents:true});`,
});
const page = await context.newPage();
const errors: string[] = [];
page.on("pageerror", (error) => errors.push(String(error)));
const checks: string[] = [];
const pass = (name: string) => {
  checks.push(name);
  console.log("PASS", name);
};
try {
  await page.goto("https://voiceflow.test/#/settings");
  const panel = page.getByRole("region", { name: "Pianificazione locale" });
  await panel
    .getByText("Pianificazione disattivata", { exact: true })
    .waitFor();
  assert.equal(await panel.getByRole("checkbox").isChecked(), false);
  assert.equal(
    await panel.getByLabel("Inizio fascia locale").inputValue(),
    "02:30",
  );
  assert.equal(
    await panel.getByLabel("Fine fascia locale").inputValue(),
    "06:00",
  );
  assert.equal(
    (
      await page.evaluate(() =>
        window.calls.filter((c) => c.cmd === "scheduler_configure"),
      )
    ).length,
    0,
  );
  pass("Default 02:30–06:00, disabled; startup reads never enable launchd");
  await panel.getByRole("checkbox").check();
  await panel.getByLabel("Inizio fascia locale").fill("03:00");
  await panel.getByLabel("Fine fascia locale").fill("05:00");
  await page.evaluate(() => {
    window.failScheduler = true;
  });
  await panel
    .getByRole("button", { name: "Salva pianificazione", exact: true })
    .click();
  await panel
    .getByText("Installazione launchd non riuscita.", { exact: false })
    .waitFor();
  await panel.getByText("Pianificazione richiesta", { exact: true }).waitFor();
  await panel.getByText("Job non rilevato in macOS", { exact: true }).waitFor();
  const configure = await page.evaluate(() =>
    window.calls.find((c) => c.cmd === "scheduler_configure"),
  );
  assert.deepEqual(configure.args, {
    schedule: { enabled: true, startMinute: 180, endMinute: 300 },
    consent: true,
  });
  assert.equal(
    (
      await page.evaluate(() =>
        window.calls.filter((c) => c.cmd === "analysis_configure"),
      )
    ).length,
    0,
  );
  pass(
    "Explicit schedule choice is separate from OpenAI and rule consent; partial launchd failure stays visible",
  );
  await page.evaluate(() => {
    window.failScheduler = false;
  });
  await panel
    .getByRole("button", { name: "Ripara pianificazione", exact: true })
    .click();
  await panel.getByText("Job caricato in macOS", { exact: true }).waitFor();
  await page.evaluate(() => {
    window.scheduler.workerRunning = true;
    window.scheduler.recording = true;
    window.scheduler.phase = "running";
    window.scheduler.retryCount = 2;
  });
  await panel
    .getByRole("button", { name: "Aggiorna stato pianificazione", exact: true })
    .click();
  await panel.getByText("Worker in esecuzione", { exact: true }).waitFor();
  await panel
    .getByText("Dettatura prioritaria attiva.", { exact: false })
    .waitFor();
  pass(
    "Repair reconciles desired/installed state; live worker, capture and retry counters rendered",
  );
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: resolve(output, "settings.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 780, height: 620 });
  await panel.scrollIntoViewIfNeeded();
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
    true,
  );
  for (const control of await panel.locator("input, button").all()) {
    const bounds = await control.boundingBox();
    if (bounds)
      assert.ok(
        bounds.x >= 0 && bounds.x + bounds.width <= 780,
        "Control clipped at the supported minimum width",
      );
  }
  await page.screenshot({
    path: resolve(output, "settings-compact.png"),
    fullPage: true,
  });
  pass(
    "Desktop and supported 780×620 window controls remain inside the viewport",
  );
  await panel
    .getByRole("button", { name: "Disattiva pianificazione", exact: true })
    .click();
  await panel
    .getByText("Pianificazione disattivata", { exact: true })
    .waitFor();
  await panel.getByText("Job non rilevato in macOS", { exact: true }).waitFor();
  await page.evaluate(() => {
    window.scheduler.helperAvailable = false;
    window.scheduler.phase = "interrupted";
  });
  await panel
    .getByRole("button", { name: "Aggiorna stato pianificazione", exact: true })
    .click();
  assert.equal(await panel.getByRole("checkbox").isDisabled(), true);
  await panel.getByText("Ultimo worker interrotto", { exact: false }).waitFor();
  pass(
    "Disable persists OFF; missing packaged helper blocks enable; interrupted worker has recovery text",
  );
  const forbidden = await page.evaluate(() =>
    window.calls.filter((c) =>
      /^(start_|toggle_|load_model|suggestions_review|analysis_run)/.test(
        c.cmd,
      ),
    ),
  );
  assert.deepEqual(forbidden, []);
  assert.deepEqual(errors, []);
  await writeFile(
    resolve(output, "result.json"),
    JSON.stringify({ checks, errors, forbidden }, null, 2),
  );
} finally {
  await browser.close();
}
