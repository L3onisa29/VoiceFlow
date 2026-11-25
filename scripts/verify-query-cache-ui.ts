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
const output = resolve(root, "artifacts/query-cache");
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
const empty={variants:[],language:'*',matching:'exact',context:{before:null,after:null,exceptBefore:[],exceptAfter:[]}};
window.dictionary={terms:[{id:1,text:'VoiceFlow',groupIds:[1],...empty,variants:['voice flow']},{id:2,text:'Promemoria',groupIds:[],...empty}],groups:[{id:1,name:'Progetto'},{id:2,name:'Lavoro'}],selectedGroupIds:[1],version:3,conflicts:[]};
mockIPC(async(cmd,args)=>{
  window.calls.push({cmd,args});
  if(cmd==='get_state')return {revision:1,phase:'recording',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:3,device:'Fixture',settings:{language:'italian',format:'spoken-commands',cleanup:true},transcript:{confirmed:'sessione attiva',partial:'',audioSeconds:3,inferenceSeconds:0,chunks:1,finished:false}};
  if(cmd==='get_permissions')return {microphone:true,microphoneStatus:'authorized',accessibility:true};
  if(cmd==='history_page'){
    const search=args.query.search;
    if(search==='old')await new Promise(resolve=>window.releaseOld=resolve);
    const text=search==='old'?'Risultato obsoleto':search==='new'?'Risultato nuovo':args.query.beforeId?'Pagina precedente':(window.historyText??'Pagina recente');
    const result={entries:[{id:args.query.beforeId?1:2,createdAt:1,text,wordCount:2,durationSeconds:1,language:'italian'}],nextCursor:search||args.query.beforeId?null:1,stats:{totalWords:4,totalSessions:2,totalSeconds:2,wordsPerMinute:120}};
    if(window.delayInitialHistory){window.delayInitialHistory=false;await new Promise(resolve=>window.releaseInitialHistory=resolve);}
    return result;
  }
  if(cmd==='suggestions_snapshot')return {proposals:[],version:0,dictionaryVersion:window.dictionary.version,groups:structuredClone(window.dictionary.groups),publications:[]};
  if(cmd==='dictionary_auto_variants')return [];
  if(cmd==='dictionary_snapshot'){
    const snapshot=structuredClone(window.dictionary);
    if(window.delayRead)await new Promise(resolve=>window.releaseRead=resolve);
    return snapshot;
  }
  if(cmd==='dictionary_select_groups'){window.dictionary.selectedGroupIds=args.ids;window.dictionary.version++;return structuredClone(window.dictionary);}
  if(cmd==='dictionary_save_term'){
    if(window.delaySave) await new Promise(resolve => window.releaseSave=resolve);
    if(window.failSave)throw new Error('Scrittura non riuscita');
    const input=args.input;
    const term={...input,id:input.id??Math.max(0,...window.dictionary.terms.map(t=>t.id))+1};
    window.dictionary.terms=window.dictionary.terms.filter(t=>t.id!==term.id).concat([term]);window.dictionary.version++;
    return structuredClone(window.dictionary);
  }
  if(cmd==='dictionary_save_group'){
    if(window.failSave)throw new Error('Scrittura non riuscita');
    const group={...args.input,id:args.input.id??Math.max(0,...window.dictionary.groups.map(g=>g.id))+1};
    window.dictionary.groups=window.dictionary.groups.filter(g=>g.id!==group.id).concat([group]);window.dictionary.version++;return structuredClone(window.dictionary);
  }
  if(cmd==='dictionary_delete_group'){
    if(window.failSave)throw new Error('Scrittura non riuscita');
    window.dictionary.groups=window.dictionary.groups.filter(g=>g.id!==args.id);
    window.dictionary.selectedGroupIds=window.dictionary.selectedGroupIds.filter(id=>id!==args.id);
    window.dictionary.terms=window.dictionary.terms.map(t=>({...t,groupIds:t.groupIds.filter(id=>id!==args.id)}));window.dictionary.version++;return structuredClone(window.dictionary);
  }
  if(cmd==='dictionary_delete_term'){
    if(window.failSave)throw new Error('Scrittura non riuscita');
    window.dictionary.terms=window.dictionary.terms.filter(t=>t.id!==args.id);window.dictionary.version++;return structuredClone(window.dictionary);
  }
  throw new Error('Unexpected IPC '+cmd);
},{shouldMockEvents:true});`,
});
const page = await context.newPage();
const errors: string[] = [];
page.on("pageerror", (e) => errors.push(String(e)));
const checks: string[] = [];
const pass = (name: string) => {
  checks.push(name);
  console.log("PASS", name);
};
const count = (cmd: string) =>
  page.evaluate(
    (cmd) =>
      (window as any).calls.filter((c: { cmd: string }) => c.cmd === cmd)
        .length,
    cmd,
  );
const refocus = () =>
  page.evaluate(() => window.dispatchEvent(new Event("focus")));
const navigate = async (name: string) => {
  await page.getByRole("link", { name, exact: true }).click();
  await page.getByRole("heading", { name, exact: true }).waitFor();
};
try {
  await page.goto("https://voiceflow.test/#/dictionary");
  await page
    .getByText("Promemoria · Nessuna variante", { exact: true })
    .waitFor();
  assert.equal(await count("dictionary_snapshot"), 1);
  await navigate("Revisione");
  // Revisione has loaded its own snapshot before we leave it.
  await page.waitForFunction(() =>
    (window as any).calls.some(
      (c: { cmd: string }) => c.cmd === "suggestions_snapshot",
    ),
  );
  await page.getByRole("tab", { name: "Proposte", exact: true }).waitFor();
  await navigate("Dizionario");
  await page
    .getByText("Promemoria · Nessuna variante", { exact: true })
    .waitFor();
  assert.equal(await count("dictionary_snapshot"), 1);
  pass(
    "Fresh dictionary cache survives route navigation: one native read instead of two",
  );

  // An unfinished read stays shared when its only consumer unmounts and returns.
  await page.evaluate(() => {
    (window as any).delayRead = true;
  });
  // No manual refresh: a window focus refetches the local queries.
  await refocus();
  await page.waitForFunction(() => !!(window as any).releaseRead);
  // A background refetch (window focus) never locks the page: clicks and
  // keystrokes right after focusing the window are taken at once.
  assert.equal(
    await page
      .getByRole("button", { name: "Aggiungi parola", exact: true })
      .isDisabled(),
    false,
  );
  const termSearch = page.locator("#dictionary-search");
  assert.equal(await termSearch.isDisabled(), false);
  await termSearch.pressSequentially("voi");
  assert.equal(await termSearch.inputValue(), "voi");
  await termSearch.fill("");
  pass("Background refetch keeps actions enabled and input responsive");
  const duringRead = await count("dictionary_snapshot");
  await navigate("Revisione");
  await navigate("Dizionario");
  assert.equal(await count("dictionary_snapshot"), duringRead);
  await page.evaluate(() => {
    const w = window as any;
    w.delayRead = false;
    w.releaseRead();
  });
  // The page unlocks its actions once the shared read settles.
  await page.waitForFunction(
    () =>
      [...document.querySelectorAll("button")].find(
        (button) => button.textContent?.trim() === "Aggiungi parola",
      )?.disabled === false,
  );
  pass("Concurrent native reads deduplicate across route remounts");

  await context.setOffline(true);
  await page.evaluate(() => {
    (window as any).dictionary.terms[1].text = "Locale offline";
  });
  await refocus();
  await page.getByText("Locale offline", { exact: true }).waitFor();
  await page.getByRole("button", { name: /^Gruppi attivi/ }).click();
  await page.locator("#active-group-2").click();
  await page.waitForFunction(() =>
    (window as any).dictionary.selectedGroupIds.includes(2),
  );
  pass("Local queries and writes continue with the browser offline");
  await page.keyboard.press("Escape");
  await page.locator("#active-group-2").waitFor({ state: "hidden" });
  await context.setOffline(false);

  await navigate("Storico");
  await page.getByText("Pagina recente", { exact: true }).waitFor();
  await page
    .getByRole("button", { name: "Pagina successiva", exact: true })
    .click();
  await page.getByText("Pagina precedente", { exact: true }).waitFor();
  assert.equal(
    await page.getByText("Pagina recente", { exact: true }).count(),
    0,
  );
  await page
    .getByRole("button", { name: "Pagina precedente", exact: true })
    .click();
  await page.getByText("Pagina recente", { exact: true }).waitFor();
  pass("History pagination switches pages and keeps the first page cached");
  const search = page.getByLabel("Cerca nelle trascrizioni", { exact: true });
  await search.fill("old");
  await page.waitForFunction(() => !!(window as any).releaseOld);
  await search.fill("new");
  await page.getByText("Risultato nuovo", { exact: true }).waitFor();
  await page.evaluate(() => (window as any).releaseOld());
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  assert.equal(
    await page.getByText("Risultato obsoleto", { exact: true }).count(),
    0,
  );
  pass("A late previous search cannot replace the current history results");
  await search.fill("");
  await page.getByText("Pagina recente", { exact: true }).waitFor();
  await navigate("Dizionario");
  await page.evaluate(async () => {
    const w = window as any;
    w.historyText = "Nuova dettatura";
    await w.__TAURI_INTERNALS__.invoke("plugin:event|emit", {
      event: "history-changed",
      payload: null,
    });
  });
  await page.getByRole("link", { name: "Storico", exact: true }).click();
  await page.getByText("Nuova dettatura", { exact: true }).waitFor();
  pass(
    "The shared native listener invalidates history while its route is unmounted",
  );
  await navigate("Dizionario");
  await page.reload();
  await page
    .getByText("Promemoria · Nessuna variante", { exact: true })
    .waitFor();
  await page.evaluate(() => {
    (window as any).delayInitialHistory = true;
  });
  await page.getByRole("link", { name: "Storico", exact: true }).click();
  await page.waitForFunction(() => !!(window as any).releaseInitialHistory);
  await page.evaluate(async () => {
    const w = window as any;
    w.historyText = "Arrivata durante la lettura";
    await w.__TAURI_INTERNALS__.invoke("plugin:event|emit", {
      event: "history-changed",
      payload: null,
    });
  });
  await page
    .getByText("Arrivata durante la lettura", { exact: true })
    .waitFor({ timeout: 5000 });
  await page.evaluate(() => (window as any).releaseInitialHistory());
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  assert.equal(
    await page.getByText("Pagina recente", { exact: true }).count(),
    0,
  );
  pass(
    "An event during the first pending history read replaces it; the late old response is ignored",
  );
  assert.deepEqual(errors, []);
  await writeFile(
    resolve(output, "result.json"),
    JSON.stringify({ checks, errors }, null, 2) + "\n",
  );
} catch (error) {
  console.error(
    await page.evaluate(() => ({
      calls: (window as any).calls,
      dictionary: (window as any).dictionary,
      text: document.body.innerText,
    })),
  );
  throw error;
} finally {
  await browser.close();
}
