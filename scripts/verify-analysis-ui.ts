// Built UI, intercepted assets + Tauri fixtures. No listener, provider, or audio.
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
const modulePath = process.env.PLAYWRIGHT_MODULE;
if (!modulePath) throw new Error("Set PLAYWRIGHT_MODULE");
const { chromium } = await import(pathToFileURL(modulePath).href);
const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "artifacts/analysis-d1");
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
window.calls=[];window.failAnalysis=false;window.excluded=false;
window.analysis={settings:{enabled:false,maxSources:20,maxInputBytes:65536,maxRequestsPerDay:2,requestTimeoutSeconds:120,maxSecondsPerDay:300},requestsToday:0,inputBytesToday:0,elapsedSecondsToday:0,reservedSecondsToday:0,pendingSources:3,excludedSessions:0,lastState:null,lastError:null,acceptedCandidates:0,rejectedCandidates:0};
const entry={id:1,createdAt:1750000000000,text:'voice flow fixture',wordCount:3,durationSeconds:10,language:'it'};
mockIPC(async(cmd,args)=>{
 window.calls.push({cmd,args});
 if(cmd==='get_state')return {revision:1,phase:'idle',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:0,device:'Fixture',settings:{language:'italian',format:'spoken-commands',cleanup:true},transcript:{confirmed:'',partial:'',audioSeconds:0,inferenceSeconds:0,chunks:0,finished:false}};
 if(cmd==='get_permissions')return {microphone:true,microphoneStatus:'authorized',accessibility:true};
 if(cmd==='dictionary_snapshot')return {terms:[],groups:[],selectedGroupIds:[],version:1,conflicts:[]};
 if(cmd==='history_page')return {entries:[entry],nextCursor:null,stats:{totalWords:3,totalSessions:1,totalSeconds:10,wordsPerMinute:18}};
 if(cmd==='history_detail')return {entry,originalAsr:[entry.text],producedText:entry.text,processing:null,editVersion:0,currentRevision:null,revisions:[]};
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
  const panel = page.getByRole("region", { name: "Analisi con OpenAI" });
  await panel.getByText("Analisi disattivata", { exact: true }).waitFor();
  assert.equal(
    await panel
      .getByRole("checkbox", { name: "Autorizzo l'invio", exact: false })
      .isChecked(),
    false,
  );
  assert.equal(
    (
      await page.evaluate(() =>
        window.calls.filter((c) => c.cmd === "analysis_configure"),
      )
    ).length,
    0,
  );
  pass(
    "Off by default; reading settings never sends consent or starts analysis",
  );
  await panel
    .getByRole("checkbox", { name: "Autorizzo l'invio", exact: false })
    .check();
  await panel.getByLabel("Fonti per lotto", { exact: true }).fill("12");
  await panel.getByLabel("Richieste al giorno", { exact: true }).fill("3");
  await panel.getByRole("button", { name: "Salva preferenze analisi" }).click();
  await panel.getByText("Consenso attivo", { exact: true }).waitFor();
  const call = await page.evaluate(() =>
    window.calls.find((c) => c.cmd === "analysis_configure"),
  );
  assert.equal(call.args.consent, true);
  assert.equal(call.args.settings.maxSources, 12);
  assert.equal(call.args.settings.maxRequestsPerDay, 3);
  pass("Explicit OpenAI consent and bounded limits saved through IPC");
  await page.evaluate(() => {
    window.failAnalysis = true;
  });
  await panel
    .getByRole("button", { name: "Disattiva analisi", exact: true })
    .click();
  await panel
    .getByText("Salvataggio analisi non riuscito", { exact: false })
    .waitFor();
  assert.equal(
    await panel.getByText("Consenso attivo", { exact: true }).count(),
    1,
  );
  await page.evaluate(() => {
    window.failAnalysis = false;
    window.analysis.requestsToday = 2;
    window.analysis.inputBytesToday = 4900;
    window.analysis.elapsedSecondsToday = 32;
    window.analysis.lastError = "candidate-rejected";
    window.analysis.rejectedCandidates = 1;
    window.analysis.acceptedCandidates = 2;
  });
  await panel.getByRole("button", { name: "Aggiorna stato analisi" }).click();
  await panel.getByText("Oggi UTC: 2 tentativi", { exact: false }).waitFor();
  await panel
    .getByText("Alcune proposte non hanno superato", { exact: false })
    .waitFor();
  pass(
    "Errors preserve persisted consent; real counters and partial outcome visible",
  );
  await panel
    .getByRole("heading", { name: "Analisi con OpenAI" })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: resolve(output, "settings.png"),
    fullPage: true,
  });
  await panel
    .getByRole("button", { name: "Disattiva analisi", exact: true })
    .click();
  await panel.getByText("Analisi disattivata", { exact: true }).waitFor();
  pass("Disable is always an explicit persisted action");
  await page.goto("https://voiceflow.test/");
  await page.getByRole("button", { name: "Apri testo", exact: true }).click();
  const dialog = page.getByRole("dialog");
  const exclude = dialog.getByRole("checkbox", {
    name: "Escludi questa sessione dall'analisi con OpenAI",
  });
  await exclude.check();
  assert.equal(await page.evaluate(() => window.excluded), true);
  await page.screenshot({
    path: resolve(output, "history-excluded.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Chiudi", exact: true }).click();
  await page.getByRole("button", { name: "Apri testo", exact: true }).click();
  assert.equal(await exclude.isChecked(), true);
  pass("Per-session exclusion remains selected on history reopen");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: resolve(output, "history-mobile.png"),
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
    true,
  );
  await page.getByRole("button", { name: "Chiudi", exact: true }).click();
  await page.goto("https://voiceflow.test/#/settings");
  await panel.getByText("Analisi disattivata", { exact: true }).waitFor();
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
    true,
  );
  await page.screenshot({
    path: resolve(output, "settings-mobile.png"),
    fullPage: true,
  });
  pass("Settings and session privacy fit mobile viewport");
  const commands = await page.evaluate(() => window.calls.map((c) => c.cmd));
  assert.ok(
    !commands.some((c) =>
      /analysis_run|analysis_prepare|start_record|load_model|suggestions_review/.test(
        c,
      ),
    ),
  );
  assert.deepEqual(errors, []);
  pass("No provider, publication, recording, or model-load IPC invoked");
  await writeFile(
    resolve(output, "result.json"),
    JSON.stringify(
      {
        checks,
        errors,
        commands,
        scope: "Production assets + mocked IPC only; no real OpenAI request",
      },
      null,
      2,
    ) + "\n",
  );
} finally {
  await browser.close();
}
