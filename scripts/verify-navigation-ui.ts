// Built UI navigation: legacy redirects, shortcuts, command palette, active states.
// Intercepted assets + Tauri fixtures. No HTTP listener or audio.
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const modulePath = process.env.PLAYWRIGHT_MODULE;
const { chromium } = await import(
  modulePath ? pathToFileURL(modulePath).href : "playwright"
);
const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "artifacts/navigation");
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
window.calls=[];
const entry={id:1,createdAt:1750000000000,text:'voice flow fixture',wordCount:3,durationSeconds:10,language:'it'};
const run={key:{batchId:1,attemptId:1},state:'completed',origin:'manual',createdAt:1750000000000,startedAt:1750000000100,endedAt:1750000001000,elapsedMs:900,sourceCount:1,accepted:0,rejected:0,error:null,inputBytes:1000};
const proposal={id:7,revision:2,state:'proposed',rejectionReason:null,approvedAt:null,conflicts:[],candidate:{definition:{category:'terminology',preferred:'VoiceFlow',variants:['voice flow'],matching:'exact',context:{before:null,after:null,exceptBefore:[],exceptAfter:[]}},language:'it',groupIds:[],reason:'Grafia del progetto',positiveExamples:[{input:'voice flow',output:'VoiceFlow'}],negativeExamples:[{input:'voice flower',output:'voice flower'}]},evidence:[{sessionId:1,revisionId:null,originalIndex:null,before:'voice flow',after:null,humanConfirmed:false}]};
mockIPC(async(cmd,args)=>{
 window.calls.push(cmd);
 if(cmd==='get_state')return {revision:1,phase:'idle',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:0,device:'Fixture',settings:{language:'italian',format:'spoken-commands',cleanup:true},transcript:{confirmed:'',partial:'',audioSeconds:0,inferenceSeconds:0,chunks:0,finished:false}};
 if(cmd==='get_permissions')return {microphone:true,microphoneStatus:'authorized',accessibility:true};
 if(cmd==='dictionary_snapshot')return {terms:[],groups:[],selectedGroupIds:[],version:1,conflicts:[]};
 if(cmd==='dictionary_auto_variants')return [];
 if(cmd==='history_page')return {entries:[entry],nextCursor:null,stats:{totalWords:3,totalSessions:1,totalSeconds:10,wordsPerMinute:18}};
 if(cmd==='history_detail')return {entry,originalAsr:null,producedText:entry.text,processing:null,editVersion:0,currentRevision:null,revisions:[]};
 if(cmd==='analysis_excluded')return false;
 if(cmd==='analysis_status')return {settings:{enabled:false,maxSources:20,maxInputBytes:65536,maxRequestsPerDay:2,requestTimeoutSeconds:120,maxSecondsPerDay:300},requestsToday:0,inputBytesToday:0,elapsedSecondsToday:0,reservedSecondsToday:0,pendingSources:0,excludedSessions:0,lastState:null,lastError:null,acceptedCandidates:0,rejectedCandidates:0};
 if(cmd==='scheduler_status')return {schedule:{enabled:false,startMinute:150,endMinute:360},installed:false,loaded:false,matchesConfiguration:false,helperAvailable:true,workerRunning:false,recording:false,phase:'idle',leaseUntil:0,nextAttempt:0,retryCount:0,lastError:null};
 if(cmd==='scheduler_invocations')return [];
 if(cmd==='analysis_runs')return {entries:[structuredClone(run)],nextCursor:null};
 if(cmd==='analysis_run_detail')return {run:structuredClone(run),attempts:[structuredClone(run)],sources:[],proposals:[],findings:[],spellings:[],rejections:[]};
 if(cmd==='suggestions_snapshot')return {proposals:[proposal],version:0,dictionaryVersion:1,groups:[],publications:[]};
 return null;
},{shouldMockEvents:true});`,
});
const page = await context.newPage();
page.setDefaultTimeout(10000);
const errors: string[] = [];
page.on("pageerror", (e: Error) => errors.push(String(e)));
const checks: string[] = [];
const pass = (name: string) => {
  checks.push(name);
  console.log("PASS", name);
};
const base = "https://voiceflow.test/#";
const nav = page.getByRole("navigation", { name: "Navigazione principale" });
const h1 = (name: string) =>
  page.getByRole("heading", { level: 1, name, exact: true });
const current = () => nav.locator('a[aria-current="page"]').allInnerTexts();
try {
  // Legacy URLs keep working, with their params and search.
  await page.goto(`${base}/suggestions?proposal=7`);
  await page.waitForURL(`${base}/review?proposal=7`);
  await h1("Revisione").waitFor();
  await page.locator('[data-proposal-id="7"][aria-current="page"]').waitFor();
  await page.locator('[data-proposal-detail="7"]').waitFor();
  await page.goto(`${base}/analysis?origin=manual`);
  await page.waitForURL(`${base}/review/runs?origin=manual`);
  await page.getByRole("link", { name: /Tentativo #1$/ }).waitFor();
  await page.goto(`${base}/analysis/1/attempts/1?origin=manual`);
  await page.waitForURL(`${base}/review/runs/1/attempts/1?origin=manual`);
  await page
    .getByRole("heading", { level: 2, name: "Tentativo #1", exact: true })
    .waitFor();
  await page.goto(`${base}/analysis/1/prepared`);
  await page.waitForURL(`${base}/review/runs/1/prepared`);
  await page.goto(`${base}/analysis/0/attempts/1`);
  await page.waitForURL(`${base}/review/runs/0/attempts/1`);
  await page
    .getByText("Il collegamento contiene un identificatore non valido.")
    .waitFor();
  await page.goto(`${base}/transcriptions/1`);
  await page.waitForURL(`${base}/history/1`);
  await h1("Trascrizione").waitFor();
  await page.goto(`${base}/transcriptions/abc`);
  await page.waitForURL(`${base}/history/abc`);
  await page.getByText("Trascrizione non trovata", { exact: true }).waitFor();
  pass(
    "Legacy /suggestions, /analysis, run and /transcriptions URLs redirect with params and search; invalid ids stay handled",
  );

  // Nested pages keep their section active, in the sidebar and in the tabs.
  await page.goto(`${base}/review/runs/1/attempts/1`);
  await page
    .getByRole("heading", { level: 2, name: "Tentativo #1", exact: true })
    .waitFor();
  assert.deepEqual(await current(), ["Revisione"]);
  assert.equal(
    await page
      .getByRole("tab", { name: "Esecuzioni", exact: true })
      .getAttribute("aria-selected"),
    "true",
  );
  assert.equal(
    await page
      .getByRole("tab", { name: "Proposte", exact: true })
      .getAttribute("aria-selected"),
    "false",
  );
  assert.equal(await page.getByRole("heading", { level: 1 }).count(), 1);
  // The header action is shared by both tabs.
  await page
    .getByRole("button", { name: "Esegui analisi ora", exact: true })
    .waitFor();
  await page.getByRole("tab", { name: "Proposte", exact: true }).click();
  await page.waitForURL(`${base}/review`);
  await page.locator('[data-proposal-id="7"]').waitFor();
  await page.locator('[data-proposal-detail="7"]').waitFor();
  await page
    .getByRole("button", { name: "Esegui analisi ora", exact: true })
    .waitFor();
  assert.equal(
    await page
      .getByRole("tab", { name: "Proposte", exact: true })
      .getAttribute("aria-selected"),
    "true",
  );
  assert.deepEqual(await current(), ["Revisione"]);
  await nav.getByLabel("1 proposta da revisionare").waitFor();
  pass(
    "Revisione stays active on nested run pages; tabs follow the router; one h1; badge counts proposals to review",
  );

  // In-app shortcuts, ignored while typing.
  await page.keyboard.press("Meta+2");
  await page.waitForURL(`${base}/history`);
  await h1("Storico").waitFor();
  assert.deepEqual(await current(), ["Storico"]);
  const search = page.getByRole("searchbox", {
    name: "Cerca nelle trascrizioni",
  });
  await search.focus();
  await page.keyboard.press("Meta+1");
  await page.keyboard.press("Meta+k");
  assert.equal(page.url(), `${base}/history`);
  assert.equal(await page.getByRole("dialog").count(), 0);
  await search.blur();
  await page.keyboard.press("Meta+3");
  await page.waitForURL(`${base}/dictionary`);
  await page.keyboard.press("Meta+Comma");
  await page.waitForURL(`${base}/settings`);
  await page.keyboard.press("Meta+1");
  await page.waitForURL(`${base}/`);
  pass("⌘1–⌘5 and ⌘, navigate; shortcuts are ignored while typing");

  // Command palette: pages, settings sections and actions.
  await page.keyboard.press("Meta+k");
  const palette = page.getByRole("dialog", { name: "Comandi" });
  await palette.waitFor();
  await palette.getByText("Vai a", { exact: true }).waitFor();
  await palette.getByText("Azioni", { exact: true }).waitFor();
  await palette
    .getByRole("option", { name: /Inizia dettatura/ })
    .locator("kbd")
    .first()
    .waitFor();
  await palette.getByRole("combobox").fill("Dizionario");
  await page.keyboard.press("Enter");
  await palette.waitFor({ state: "hidden" });
  await page.waitForURL(`${base}/dictionary`);
  await h1("Dizionario").waitFor();
  await page.keyboard.press("Meta+k");
  await palette.getByRole("combobox").fill("Automazione");
  await palette
    .getByRole("option", { name: /Impostazioni: Automazione/ })
    .click();
  await page.waitForURL(`${base}/settings?section=automation`);
  await page.keyboard.press("Meta+k");
  await palette.getByRole("option", { name: /Aggiungi parola/ }).click();
  await page
    .getByRole("dialog", { name: "Aggiungi parola", exact: true })
    .waitFor();
  await page.waitForURL(`${base}/dictionary`);
  await page.keyboard.press("Escape");
  pass(
    "⌘K palette opens with shortcut hints, navigates to pages and settings sections, and opens Aggiungi parola",
  );

  // Compact system status and draggable top edge.
  await page
    .getByRole("button", { name: "Stato del sistema: Pronto", exact: true })
    .click();
  await page.getByText("Motore · Nemotron 3.5", { exact: true }).waitFor();
  await page
    .getByRole("link", { name: "Apri impostazioni di sistema" })
    .click();
  await page.waitForURL(`${base}/settings?section=system`);
  for (const path of ["/", "/settings"]) {
    await page.goto(`${base}${path}`);
    await page.getByRole("link").first().waitFor();
    for (const x of [120, 600, 1000])
      assert.equal(
        await page.evaluate(
          ([x, y]) =>
            document
              .elementFromPoint(x, y)
              ?.hasAttribute("data-tauri-drag-region") ?? false,
          [x, 12],
        ),
        true,
        `${path} top edge at x=${x} is not draggable`,
      );
  }
  pass(
    "System status opens its popover and system settings; the top edge drags the window on every page",
  );
  await page.screenshot({ path: resolve(output, "navigation.png") });
  assert.deepEqual(errors, []);
  pass("Nessun errore nel browser");
} catch (error) {
  await page.screenshot({ path: resolve(output, "failure.png") });
  console.log(await page.locator("body").innerText());
  throw error;
} finally {
  await browser.close();
}
await writeFile(
  resolve(output, "result.json"),
  JSON.stringify({ checks }, null, 2) + "\n",
);
