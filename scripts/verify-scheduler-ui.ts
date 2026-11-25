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
 if(cmd==='scheduler_status'){if(window.failSchedulerRead)throw new Error('Pianificazione non disponibile');return structuredClone(window.scheduler);}
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
  const configureCalls = () =>
    page.evaluate(() =>
      window.calls
        .filter((c) => c.cmd === "scheduler_configure")
        .map((c) => c.args),
    );
  const waitForConfigureCount = (count: number) =>
    page.waitForFunction(
      (count) =>
        window.calls.filter((c) => c.cmd === "scheduler_configure").length ===
        count,
      count,
    );
  const waitForChecked = (checked: boolean) =>
    page.waitForFunction(
      (state) =>
        document
          .getElementById("scheduler-consent")
          ?.getAttribute("data-state") === state,
      checked ? "checked" : "unchecked",
    );
  await page.goto("https://voiceflow.test/#/settings?section=automation");
  const panel = page.getByRole("region", { name: "Automazione", exact: true });
  const toggle = panel.getByRole("checkbox", {
    name: "Analisi programmata",
    exact: true,
  });
  await toggle.waitFor();
  assert.equal(await panel.getByRole("checkbox").count(), 1);
  assert.equal(await toggle.isChecked(), false);
  const start = panel.getByLabel("Inizio fascia", { exact: true });
  const end = panel.getByLabel("Fine fascia", { exact: true });
  assert.equal(await start.inputValue(), "02:30");
  assert.equal(await end.inputValue(), "06:00");
  assert.equal(await panel.getByText("Pronta", { exact: true }).count(), 0);
  assert.equal((await configureCalls()).length, 0);
  pass("Default 02:30–06:00, disabled; startup reads never enable launchd");
  // A window longer than 12 hours is refused before reaching launchd.
  await start.fill("17:00");
  await start.press("Enter");
  await panel
    .getByText("La fascia deve durare da 1 minuto a 12 ore.")
    .waitFor();
  assert.equal(await start.getAttribute("aria-invalid"), "true");
  assert.equal((await configureCalls()).length, 0);
  await start.press("Escape");
  assert.equal(await start.inputValue(), "02:30");
  await start.fill("03:00");
  await start.press("Enter");
  await waitForConfigureCount(1);
  await end.fill("05:00");
  await end.press("Enter");
  await waitForConfigureCount(2);
  assert.deepEqual(await configureCalls(), [
    {
      schedule: { enabled: false, startMinute: 180, endMinute: 360 },
      consent: false,
    },
    {
      schedule: { enabled: false, startMinute: 180, endMinute: 300 },
      consent: false,
    },
  ]);
  assert.equal(await toggle.isChecked(), false);
  const navigation = page.getByRole("navigation", {
    name: "Sezioni impostazioni",
  });
  await navigation
    .getByRole("link", { name: "Dettatura", exact: true })
    .click();
  await navigation
    .getByRole("link", { name: "Automazione", exact: true })
    .click();
  assert.equal(await start.inputValue(), "03:00");
  assert.equal(await end.inputValue(), "05:00");
  pass(
    "Window edits are validated, apply on Enter without enabling the schedule, and survive category navigation",
  );
  await page.evaluate(() => {
    window.failScheduler = true;
  });
  await toggle.click();
  const dialog = page.getByRole("alertdialog", {
    name: "Attivare l'analisi programmata?",
  });
  await dialog.getByText(/tra le 03:00 e le 05:00/).waitFor();
  await dialog.getByRole("button", { name: "Annulla", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  assert.equal(await toggle.isChecked(), false);
  assert.equal((await configureCalls()).length, 2);
  await toggle.click();
  await dialog.getByRole("button", { name: "Attiva", exact: true }).click();
  await waitForConfigureCount(3);
  await panel
    .getByText("Installazione launchd non riuscita.", { exact: false })
    .waitFor();
  await waitForChecked(true);
  await panel.getByText("Da riparare", { exact: true }).waitFor();
  assert.deepEqual((await configureCalls()).at(-1), {
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
    "Explicit schedule choice is confirmed and separate from OpenAI and rule consent; partial launchd failure stays visible",
  );
  await page.evaluate(() => {
    window.failScheduler = false;
  });
  await panel
    .getByRole("button", { name: "Ripara pianificazione", exact: true })
    .click();
  await panel.getByText("Pronta", { exact: true }).waitFor();
  assert.equal(
    await panel
      .getByText("Installazione launchd non riuscita.", { exact: false })
      .count(),
    0,
  );
  await page.evaluate(() => {
    window.scheduler.workerRunning = true;
    window.scheduler.recording = true;
    window.scheduler.phase = "running";
    window.scheduler.retryCount = 2;
  });
  await panel
    .getByRole("button", { name: "Aggiorna stato pianificazione", exact: true })
    .click();
  await panel.getByText("Analisi in corso", { exact: true }).waitFor();
  await panel
    .getByText("Dettatura prioritaria attiva", { exact: true })
    .waitFor();
  await panel
    .getByRole("button", { name: "Avanzate · Diagnostica automazione" })
    .click();
  await panel.getByText(/oggi \(UTC\): 2 su 3/).waitFor();
  await panel
    .getByRole("button", { name: "Avanzate · Diagnostica automazione" })
    .click();
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
  await page.evaluate(() => {
    window.failScheduler = true;
  });
  await toggle.click();
  await waitForConfigureCount(5);
  // Disabling never asks for confirmation.
  assert.equal(await page.getByRole("alertdialog").count(), 0);
  assert.deepEqual((await configureCalls()).at(-1), {
    schedule: { enabled: false, startMinute: 180, endMinute: 300 },
    consent: false,
  });
  await waitForChecked(false);
  await panel
    .getByRole("button", { name: "Riprova disattivazione", exact: true })
    .waitFor();
  await page.evaluate(() => {
    window.failScheduler = false;
  });
  await panel
    .getByRole("button", { name: "Riprova disattivazione", exact: true })
    .click();
  await waitForConfigureCount(6);
  assert.equal((await configureCalls()).at(-1).consent, false);
  await waitForChecked(false);
  assert.equal(
    await panel
      .getByRole("button", { name: "Riprova disattivazione", exact: true })
      .count(),
    0,
  );
  pass(
    "Failed removal keeps scheduling disabled and offers an actionable retry",
  );
  assert.equal(
    await panel.getByText("Da riparare", { exact: true }).count(),
    0,
  );
  await page.evaluate(() => {
    window.scheduler.helperAvailable = false;
    window.scheduler.phase = "interrupted";
  });
  await panel
    .getByRole("button", { name: "Aggiorna stato pianificazione", exact: true })
    .click();
  assert.equal(await panel.getByRole("checkbox").isDisabled(), true);
  await panel
    .getByRole("button", { name: "Avanzate · Diagnostica automazione" })
    .click();
  await panel
    .getByText("Ultima analisi interrotta", { exact: false })
    .waitFor();
  pass(
    "Disable persists OFF; missing packaged helper blocks enable; interrupted worker has recovery text",
  );
  await page.evaluate(() => {
    window.failSchedulerRead = true;
  });
  await page
    .getByRole("link", { name: "Torna a VoiceFlow", exact: true })
    .click();
  await page.getByRole("link", { name: "Impostazioni", exact: true }).click();
  await page
    .getByRole("navigation", { name: "Sezioni impostazioni" })
    .getByRole("link", { name: "Automazione", exact: true })
    .click();
  await panel
    .getByRole("button", { name: "Riprova pianificazione", exact: true })
    .waitFor();
  await page.evaluate(() => {
    window.failSchedulerRead = false;
  });
  await panel
    .getByRole("button", { name: "Riprova pianificazione", exact: true })
    .click();
  await panel
    .getByRole("checkbox", { name: "Analisi programmata", exact: true })
    .waitFor();
  pass(
    "Schedule read failure exposes retry and recovers without enabling automation",
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
