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
window.calls=[];window.failAnalysis=false;window.excluded=false;window.permissionDenied=false;window.modelReady=true;window.dictationSettings={language:'italian',format:'spoken-commands',cleanup:true};
window.scheduler={schedule:{enabled:false,startMinute:150,endMinute:360},installed:false,loaded:false,matchesConfiguration:false,helperAvailable:true,workerRunning:false,recording:false,phase:'idle',leaseUntil:0,nextAttempt:0,retryCount:0,lastError:null};
window.analysis={settings:{enabled:false,maxSources:20,maxInputBytes:65536,maxRequestsPerDay:2,requestTimeoutSeconds:120,maxSecondsPerDay:300},requestsToday:0,inputBytesToday:0,elapsedSecondsToday:0,reservedSecondsToday:0,pendingSources:3,excludedSessions:0,lastState:null,lastError:null,acceptedCandidates:0,rejectedCandidates:0};
const entry={id:1,createdAt:1750000000000,text:'voice flow fixture',wordCount:3,durationSeconds:10,language:'it'};
mockIPC(async(cmd,args)=>{
 window.calls.push({cmd,args});
 if(cmd==='get_state')return {revision:1,phase:'idle',modelReady:window.modelReady,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:0,device:'Fixture',settings:window.dictationSettings,transcript:{confirmed:'',partial:'',audioSeconds:0,inferenceSeconds:0,chunks:0,finished:false}};
 if(cmd==='get_permissions')return {microphone:!window.permissionDenied,microphoneStatus:window.permissionDenied?'denied':'authorized',accessibility:!window.permissionDenied};
 if(cmd==='scheduler_status')return structuredClone(window.scheduler);
 if(cmd==='save_settings'){window.dictationSettings=structuredClone(args.settings);return null;}
 if(cmd==='dictionary_snapshot')return {terms:[],groups:[],selectedGroupIds:[],version:1,conflicts:[]};
 if(cmd==='history_page')return {entries:[entry],nextCursor:null,stats:{totalWords:3,totalSessions:1,totalSeconds:10,wordsPerMinute:18}};
 if(cmd==='history_detail')return {entry,originalAsr:[entry.text],producedText:entry.text,processing:null,editVersion:0,currentRevision:null,revisions:[]};
 if(cmd==='analysis_status'){if(window.failAnalysisRead)throw new Error('Preferenze analisi non disponibili');return structuredClone(window.analysis);}
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
      window.calls.filter((c) => c.cmd === "analysis_configure"),
    );
  const lastConfigure = () =>
    page.evaluate(() =>
      window.calls.findLast((c) => c.cmd === "analysis_configure"),
    );
  const waitForConfigureCount = (count: number) =>
    page.waitForFunction(
      (count) =>
        window.calls.filter((c) => c.cmd === "analysis_configure").length ===
        count,
      count,
    );
  await page.goto("https://voiceflow.test/#/settings?section=analysis");
  const panel = page.getByRole("region", { name: "Analisi", exact: true });
  const consent = panel.getByRole("checkbox", {
    name: "Autorizzo l'invio",
    exact: false,
  });
  await consent.waitFor();
  assert.equal(await consent.isChecked(), false);
  assert.equal((await configureCalls()).length, 0);
  pass(
    "Off by default; reading settings never sends consent or starts analysis",
  );
  assert.deepEqual(
    await page.getByRole("heading", { level: 1 }).allTextContents(),
    ["Analisi"],
  );
  assert.deepEqual(
    await panel.getByRole("heading", { level: 2 }).allTextContents(),
    ["Invio a OpenAI", "Modello"],
  );
  assert.equal(
    await panel.getByLabel("Fonti per lotto", { exact: true }).count(),
    0,
  );
  assert.equal(
    await panel
      .getByRole("button", { name: "Cosa viene inviato e cosa resta" })
      .getAttribute("aria-expanded"),
    "false",
  );
  assert.equal(
    await page
      .getByRole("button", {
        name: "Avanzate · Diagnostica sistema",
        includeHidden: true,
      })
      .getAttribute("aria-expanded"),
    "false",
  );
  assert.equal(
    await panel.getByRole("button", { name: /Salva/ }).count(),
    0,
    "No separate save button: every control applies on change",
  );
  const navigation = page.getByRole("navigation", {
    name: "Sezioni impostazioni",
  });
  for (const group of ["Automazione", "Sistema", "Dettatura", "Analisi"]) {
    await navigation.getByRole("link", { name: group, exact: true }).click();
    await page.waitForFunction(
      (name) => document.activeElement?.textContent === name,
      group,
    );
  }
  pass(
    "One h1 per group; four directly reachable groups; details collapsed; navigation transfers keyboard focus",
  );
  const model = panel.getByLabel("Modello di analisi", { exact: true });
  const reasoning = panel.getByRole("combobox", {
    name: "Ragionamento",
    exact: true,
  });
  assert.equal(await model.inputValue(), "gpt-6-luna");
  assert.equal(await reasoning.innerText(), "Medio · predefinito");
  await model.fill("gpt 6 luna");
  await model.press("Enter");
  await panel
    .getByText("Usa lettere, numeri, punti, trattini o trattini bassi.")
    .waitFor();
  assert.equal(await model.getAttribute("aria-invalid"), "true");
  await model.blur();
  assert.equal((await configureCalls()).length, 0);
  await model.fill("gpt-6-sol");
  await model.press("Enter");
  await waitForConfigureCount(1);
  await reasoning.click();
  await page.getByRole("option", { name: "Basso", exact: true }).click();
  await waitForConfigureCount(2);
  assert.equal(await reasoning.innerText(), "Basso");
  pass(
    "Legacy defaults show Luna/medium; invalid model is never sent; valid model and reasoning apply on Enter and on change",
  );
  await navigation
    .getByRole("link", { name: "Automazione", exact: true })
    .click();
  await navigation.getByRole("link", { name: "Analisi", exact: true }).click();
  assert.equal(await model.inputValue(), "gpt-6-sol");
  assert.equal(await reasoning.innerText(), "Basso");
  const search = page.getByRole("searchbox", { name: "Cerca impostazioni" });
  await search.fill("microfono");
  assert.deepEqual(await navigation.getByRole("link").allTextContents(), [
    "Sistema",
  ]);
  await navigation.getByRole("link", { name: "Sistema", exact: true }).click();
  assert.equal(await search.inputValue(), "");
  await search.fill("inesistente");
  await page.getByText("Nessuna impostazione trovata.").waitFor();
  await search.press("Escape");
  assert.equal(await navigation.getByRole("link").count(), 4);
  await navigation.getByRole("link", { name: "Analisi", exact: true }).click();
  await page.goBack();
  await page.getByRole("heading", { name: "Sistema", exact: true }).waitFor();
  await page.goForward();
  await panel.getByRole("heading", { name: "Analisi", exact: true }).waitFor();
  pass(
    "Search, history navigation and applied analysis preferences survive category changes",
  );
  for (const call of await configureCalls()) {
    assert.equal(call.args.consent, false);
    assert.equal(call.args.settings.enabled, false);
  }
  const disabledCall = await lastConfigure();
  assert.equal(disabledCall.args.settings.model, "gpt-6-sol");
  assert.equal(disabledCall.args.settings.reasoningEffort, "low");
  assert.equal(await consent.isChecked(), false);
  pass("Model preferences save while analysis remains disabled");
  await consent.click();
  const consentDialog = page.getByRole("alertdialog", {
    name: "Inviare i testi a OpenAI?",
  });
  await consentDialog.getByText(/La dettatura resta locale/).waitFor();
  await consentDialog.getByRole("button", { name: "Annulla" }).click();
  await consentDialog.waitFor({ state: "hidden" });
  assert.equal(await consent.isChecked(), false);
  assert.equal((await configureCalls()).length, 2);
  await consent.click();
  await consentDialog
    .getByRole("button", { name: "Autorizza invio", exact: true })
    .click();
  await waitForConfigureCount(3);
  await page.waitForFunction(
    () =>
      (
        document.getElementById("analysis-consent") as HTMLElement | null
      )?.getAttribute("data-state") === "checked",
  );
  const consentCall = await lastConfigure();
  assert.equal(consentCall.args.consent, true);
  assert.equal(consentCall.args.settings.enabled, true);
  assert.equal(consentCall.args.settings.model, "gpt-6-sol");
  assert.equal(consentCall.args.settings.reasoningEffort, "low");
  pass(
    "Enabling OpenAI consent asks first; cancelling sends nothing; confirming sends consent with the saved model",
  );
  const advanced = panel.getByRole("button", {
    name: "Avanzate · Limiti e utilizzo",
  });
  await advanced.click();
  const sources = panel.getByLabel("Fonti per lotto", { exact: true });
  await sources.fill("0");
  await sources.press("Enter");
  await panel.getByText("Inserisci un numero intero. Da 1 a 100.").waitFor();
  assert.equal(await sources.getAttribute("aria-invalid"), "true");
  assert.equal(await advanced.getAttribute("aria-expanded"), "true");
  assert.equal((await configureCalls()).length, 3);
  pass("Invalid limit stays in its field with an error and is never sent");
  await sources.fill("12");
  await sources.press("Enter");
  await waitForConfigureCount(4);
  const requests = panel.getByLabel("Richieste al giorno", { exact: true });
  await requests.fill("3");
  await requests.press("Enter");
  await waitForConfigureCount(5);
  const call = await lastConfigure();
  assert.equal(call.args.consent, true);
  assert.equal(call.args.settings.maxSources, 12);
  assert.equal(call.args.settings.maxRequestsPerDay, 3);
  assert.equal(call.args.settings.model, "gpt-6-sol");
  assert.equal(call.args.settings.reasoningEffort, "low");
  await panel.getByRole("button", { name: "Aggiorna stato analisi" }).click();
  await page.waitForFunction(
    () => window.calls.filter((c) => c.cmd === "analysis_status").length >= 2,
  );
  assert.equal(await model.inputValue(), "gpt-6-sol");
  assert.equal(await reasoning.innerText(), "Basso");
  assert.equal(await sources.inputValue(), "12");
  pass(
    "Each limit applies with the full merged settings; model and reasoning survive settings reload",
  );
  await model.scrollIntoViewIfNeeded();
  await page.screenshot({ path: resolve(output, "model-reasoning.png") });
  pass("Explicit OpenAI consent and bounded limits saved through IPC");
  const autoApprove = panel.getByRole("checkbox", {
    name: "Approva automaticamente con confidenza superiore a 90",
    exact: true,
  });
  await autoApprove.click();
  const approveDialog = page.getByRole("alertdialog", {
    name: "Approvare automaticamente le regole?",
  });
  await approveDialog
    .getByRole("button", { name: "Attiva approvazione", exact: true })
    .click();
  await waitForConfigureCount(6);
  const approveCall = await lastConfigure();
  assert.equal(approveCall.args.settings.autoApproveHighConfidence, true);
  assert.equal(approveCall.args.consent, true);
  await autoApprove.click();
  await waitForConfigureCount(7);
  assert.equal(await approveDialog.count(), 0);
  assert.equal(
    (await lastConfigure()).args.settings.autoApproveHighConfidence,
    false,
  );
  pass("Auto-approval asks before enabling and turns off immediately");
  await page.evaluate(() => {
    window.failAnalysis = true;
  });
  await consent.click();
  await page.getByText("Salvataggio analisi non riuscito").waitFor();
  await waitForConfigureCount(8);
  assert.equal(await consent.isChecked(), true);
  assert.equal(
    await page.evaluate(() => window.analysis.settings.enabled),
    true,
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
  await page.evaluate(() => {
    window.analysis.lastState = "failed";
    window.analysis.lastError = "all-candidates-rejected";
    window.analysis.acceptedCandidates = 0;
  });
  await panel.getByRole("button", { name: "Aggiorna stato analisi" }).click();
  await panel
    .getByText(
      "Nessuna proposta ha superato la validazione. Le sorgenti restano da analizzare; il lotto può essere riprovato.",
      { exact: true },
    )
    .waitFor();
  pass("Entirely invalid output explains that sources remain retryable");
  await panel
    .getByRole("heading", { name: "Analisi", exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: resolve(output, "settings.png"),
    fullPage: true,
  });
  await consent.click();
  await waitForConfigureCount(9);
  assert.equal(await page.getByRole("alertdialog").count(), 0);
  await page.waitForFunction(
    () =>
      (
        document.getElementById("analysis-consent") as HTMLElement | null
      )?.getAttribute("data-state") === "unchecked",
  );
  const offCall = await lastConfigure();
  assert.equal(offCall.args.consent, false);
  assert.equal(offCall.args.settings.enabled, false);
  pass("Disable applies immediately without confirmation and is persisted");
  await page.goto("https://voiceflow.test/#/history");
  // Each Storico row is one link that opens the transcription.
  const historyRow = () =>
    page
      .getByRole("region", { name: "Trascrizioni", exact: true })
      .getByRole("link", { name: /voice flow fixture/ });
  await historyRow().click();
  const detail = page.locator('section[aria-labelledby="transcription-title"]');
  const exclude = detail.getByRole("checkbox", {
    name: "Escludi questa sessione dall'analisi con OpenAI",
  });
  // Session privacy lives in the Dettagli tab of the transcription.
  const details = () =>
    detail.getByRole("tab", { name: "Dettagli", exact: true }).click();
  await details();
  await exclude.check();
  assert.equal(await page.evaluate(() => window.excluded), true);
  await page.screenshot({
    path: resolve(output, "history-excluded.png"),
    fullPage: true,
  });
  await detail.getByRole("link", { name: "Torna allo storico" }).click();
  await historyRow().click();
  await details();
  // The checkbox stays disabled until the persisted value is read.
  await page.waitForFunction(
    () =>
      document
        .getElementById("analysis-exclude-1")
        ?.hasAttribute("disabled") === false,
  );
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
  await detail.getByRole("link", { name: "Torna allo storico" }).click();
  await page.goto("https://voiceflow.test/#/settings?section=analysis");
  await consent.waitFor();
  assert.equal(await consent.isChecked(), false);
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
  await page.setViewportSize({ width: 1060, height: 760 });
  await navigation
    .getByRole("link", { name: "Dettatura", exact: true })
    .click();
  await page.reload();
  await page.getByRole("heading", { name: "Dettatura", exact: true }).waitFor();
  await page.screenshot({
    path: resolve(output, "settings-overview-1060.png"),
  });
  for (const viewport of [
    { width: 1060, height: 760 },
    { width: 780, height: 620 },
  ]) {
    await page.setViewportSize(viewport);
    for (const group of ["Dettatura", "Analisi", "Automazione", "Sistema"]) {
      await navigation.getByRole("link", { name: group, exact: true }).click();
      const navBounds = await navigation.boundingBox();
      assert.ok(
        navBounds &&
          navBounds.y >= 38 &&
          navBounds.y + navBounds.height <= viewport.height,
        `Group navigation remains visible: ${group} ${JSON.stringify(viewport)} ${JSON.stringify(navBounds)}`,
      );
      await page.screenshot({
        path: resolve(
          output,
          `settings-${group.toLowerCase()}-${viewport.width}.png`,
        ),
      });
    }
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );
  }
  await navigation
    .getByRole("link", { name: "Dettatura", exact: true })
    .click();
  await page.getByRole("combobox", { name: "Lingua", exact: true }).click();
  await page.getByRole("option", { name: "English", exact: true }).click();
  await page.waitForFunction(() =>
    window.calls.some(
      (c) =>
        c.cmd === "save_settings" && c.args.settings.language === "english",
    ),
  );
  pass("Dictation preference uses immediate saving without analysis consent");
  await navigation.getByRole("link", { name: "Sistema", exact: true }).click();
  const system = page.getByRole("region", { name: "Sistema", exact: true });
  assert.equal(await system.getByRole("alert").count(), 0);
  assert.equal(
    await system.getByText("Consentito", { exact: true }).count(),
    2,
  );
  await system.getByText("Caricato", { exact: true }).waitFor();
  assert.equal(
    await system.getByText("Ingresso audio", { exact: false }).count(),
    0,
    "Diagnostics stay inside the collapsed Avanzate",
  );
  await system.getByRole("button", { name: "Apri permessi microfono" }).click();
  await system
    .getByRole("button", { name: "Apri permessi accessibilità" })
    .click();
  await page.evaluate(() => {
    window.permissionDenied = true;
    window.modelReady = false;
  });
  // Deliver the same state event and permission refresh as the native app.
  await page.evaluate(async () => {
    const state = await window.__TAURI_INTERNALS__.invoke("get_state");
    await window.__TAURI_INTERNALS__.invoke("plugin:event|emit", {
      event: "dictation-state",
      payload: state,
    });
    window.dispatchEvent(new Event("focus"));
  });
  await system
    .getByRole("button", { name: "Carica modello", exact: true })
    .waitFor();
  await system.getByRole("button", { name: "Consenti microfono" }).waitFor();
  await system.getByText("Da consentire", { exact: true }).first().waitFor();
  await system.getByText("Non caricato", { exact: true }).waitFor();
  await navigation.getByRole("link", { name: "Sistema", exact: true }).click();
  await page.screenshot({
    path: resolve(output, "settings-system-errors-780.png"),
  });
  pass(
    "Healthy system is compact; permission failures expose actions; all four groups fit desktop and 780×620",
  );
  await page.evaluate(() => {
    window.failAnalysisRead = true;
  });
  await page
    .getByRole("link", { name: "Torna a VoiceFlow", exact: true })
    .click();
  await page.getByRole("link", { name: "Impostazioni", exact: true }).click();
  await navigation.getByRole("link", { name: "Analisi", exact: true }).click();
  await panel
    .getByRole("button", { name: "Riprova analisi", exact: true })
    .waitFor();
  await page.evaluate(() => {
    window.failAnalysisRead = false;
  });
  await panel
    .getByRole("button", { name: "Riprova analisi", exact: true })
    .click();
  await panel
    .getByRole("checkbox", { name: "Autorizzo l'invio", exact: false })
    .waitFor();
  pass(
    "Settings read failure exposes retry and recovers without configuring consent",
  );
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
