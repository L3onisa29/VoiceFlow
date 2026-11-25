// Production UI verification with Tauri IPC fixtures. No HTTP listener, microphone or audio.
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const modulePath = process.env.PLAYWRIGHT_MODULE;
const { chromium } = await import(
  modulePath ? pathToFileURL(modulePath).href : "playwright"
);
const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "artifacts/voice-profile");
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
window.calls=[];window.listeners={};window.available=sessionStorage.getItem('unavailable')!=='1';window.recording=null;
window.state={revision:1,phase:'idle',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:0,device:'Fixture',settings:{language:'auto',format:'faithful',cleanup:false},transcript:{confirmed:'',partial:'',audioSeconds:0,inferenceSeconds:0,chunks:0,finished:false}};
window.prompts=[
  {id:'p001',text:'Apri il bottom sheet e sistema la top bar.',split:'train',skipped:false,recording:null},
  {id:'p002',text:'Carica la build su TestFlight con rebase.',split:'test',skipped:false,recording:null},
  {id:'p003',text:'Avvia un thread per il commit.',split:'train',skipped:false,recording:null}];
window.profileStatus=()=>{const saved=window.prompts.filter(p=>p.recording);return structuredClone({available:window.available,folder:'/fixture/VoiceFlow/voice-profile',language:'it-IT',prompts:window.available?window.prompts:[],recorded:saved.length,recordedTest:saved.filter(p=>p.split==='test').length,seconds:saved.reduce((s,p)=>s+p.recording.duration,0),recording:window.recording});};
window.send=(event,payload)=>{const h=window.listeners[event];if(h!==undefined)window.__TAURI_INTERNALS__.runCallback(h,{event,id:h,payload});};
mockIPC(async(cmd,args)=>{
  window.calls.push({cmd,args});
  if(cmd==='plugin:event|listen'){window.listeners[args.event]=args.handler;return args.handler;}
  if(cmd==='plugin:event|unlisten'){delete window.listeners[args.event];return;}
  if(cmd==='get_state')return structuredClone(window.state);
  if(cmd==='get_permissions')return {microphone:true,microphoneStatus:'authorized',accessibility:true};
  if(cmd==='history_page')return {entries:[],nextCursor:null,stats:{totalWords:0,totalSessions:0,totalSeconds:0,wordsPerMinute:0}};
  if(cmd==='voice_profile_status')return window.profileStatus();
  if(cmd==='voice_profile_start'){if(window.failStart)throw new Error('Consenti a VoiceFlow di usare il microfono');window.recording=args.id;return;}
  if(cmd==='voice_profile_stop'){
    const id=window.recording;const prompt=window.prompts.find(p=>p.id===id);
    setTimeout(()=>{prompt.recording={duration:3.5,hypothesis:'apri il botton shit e sistema la top bar',hypothesisLanguage:'auto',recordedAt:1750000000000,device:'Fixture'};prompt.skipped=false;window.recording=null;window.send('voice-profile',{kind:'saved',id});},30);
    return;
  }
  if(cmd==='voice_profile_cancel'){const id=window.recording;window.recording=null;setTimeout(()=>window.send('voice-profile',{kind:'cancelled',id}),10);return;}
  if(cmd==='voice_profile_skip'){window.prompts.find(p=>p.id===args.id).skipped=args.skipped;return window.profileStatus();}
  if(cmd==='voice_profile_delete'){window.prompts.forEach(p=>{p.recording=null;p.skipped=false;});return window.profileStatus();}
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
const calls = (cmd: string) =>
  page.evaluate(
    (cmd) =>
      (window as any).calls
        .filter((c: { cmd: string }) => c.cmd === cmd)
        .map((c: { args: unknown }) => c.args),
    cmd,
  );
try {
  await page.goto("https://voiceflow.test/#/voice-profile");
  await page
    .getByRole("heading", { name: "Profilo vocale", exact: true })
    .waitFor();
  const card = page.getByLabel("Frase da leggere");
  const progress = page.getByLabel("Avanzamento del profilo");
  await card.getByText("Apri il bottom sheet e sistema la top bar.").waitFor();
  await card.getByText("Frase 1 di 3", { exact: true }).waitFor();
  await card.getByText("Addestramento", { exact: true }).waitFor();
  await progress.getByText(/0 di 3 frasi registrate/).waitFor();
  assert.equal(
    await page
      .getByRole("link", { name: "Profilo vocale", exact: true })
      .count(),
    1,
  );
  assert.deepEqual(await calls("voice_profile_start"), []);
  pass(
    "Page is reachable from the navigation and shows one sentence with its split and progress; reading it starts nothing",
  );

  assert.equal(
    await page
      .getByText("/fixture/VoiceFlow/voice-profile", { exact: false })
      .count(),
    0,
    "Storage details start collapsed",
  );
  // Space starts the reading from anywhere outside text fields and buttons.
  await card.getByText("Apri il bottom sheet e sistema la top bar.").click();
  await page.keyboard.press("Space");
  await card.getByText("In registrazione", { exact: true }).waitFor();
  assert.deepEqual(await calls("voice_profile_start"), [{ id: "p001" }]);
  await page.evaluate(() =>
    (window as any).send("voice-profile", {
      kind: "progress",
      id: "p001",
      seconds: 1.24,
      level: 0.05,
    }),
  );
  await card.getByText("Registrazione · 1.2 s", { exact: true }).waitFor();
  assert.equal(
    await card.getByRole("meter").getAttribute("aria-valuenow"),
    "60",
  );
  assert.equal(
    await card.getByRole("button", { name: "Frase successiva" }).isDisabled(),
    true,
  );
  await card.getByRole("button", { name: "Interrompi", exact: true }).click();
  // Saved: the next sentence still to read is shown and the counters move.
  await card.getByText("Carica la build su TestFlight con rebase.").waitFor();
  await card.getByText("Prova", { exact: true }).waitFor();
  await progress.getByText(/1 di 3 frasi registrate · 4 s di audio/).waitFor();
  await progress.getByText(/0 di 1 di prova/).waitFor();
  const bar = page.getByRole("progressbar", { name: "Frasi registrate" });
  assert.equal(await bar.getAttribute("aria-valuenow"), "1");
  assert.equal(await bar.getAttribute("aria-valuemax"), "3");
  assert.equal((await calls("voice_profile_stop")).length, 1);
  pass(
    "Space starts a reading; recording shows live level and time, locks navigation, and saving advances to the next unread sentence",
  );

  await card.getByRole("button", { name: "Salta", exact: true }).click();
  await card.getByText("Avvia un thread per il commit.").waitFor();
  await progress.getByText(/1 saltate/).waitFor();
  assert.deepEqual(await calls("voice_profile_skip"), [
    { id: "p002", skipped: true },
  ]);
  await page.keyboard.press("ArrowLeft");
  await card.getByText("Saltata", { exact: true }).waitFor();
  await card
    .getByRole("button", { name: "Rimetti in coda", exact: true })
    .waitFor();
  await card.getByRole("button", { name: "Frase precedente" }).click();
  await card.getByText("Registrata · 4 s", { exact: true }).waitFor();
  await card.getByText("Il modello attuale ha capito").waitFor();
  await card.getByText("apri il botton shit e sistema la top bar").waitFor();
  // A recorded sentence can be read again, not skipped.
  await card.getByRole("button", { name: "Rifai", exact: true }).waitFor();
  assert.equal(await card.getByRole("button", { name: "Salta" }).count(), 0);
  await card.getByRole("button", { name: "Rifai", exact: true }).click();
  await card.getByText("In registrazione", { exact: true }).waitFor();
  await card.getByRole("button", { name: "Annulla", exact: true }).click();
  await card.getByText("Registrata · 4 s", { exact: true }).waitFor();
  assert.equal((await calls("voice_profile_cancel")).length, 1);
  pass(
    "Skip, bring back, navigate with ← and buttons, see what the model heard, redo and cancel a redo without losing the saved take",
  );

  await page.evaluate(() => {
    (window as any).failStart = true;
  });
  await card.getByRole("button", { name: "Rifai", exact: true }).click();
  await page.getByText(/Consenti a VoiceFlow di usare il microfono/).waitFor();
  await page.evaluate(() => {
    const w = window as any;
    w.failStart = false;
    w.send("voice-profile", {
      kind: "error",
      id: "p003",
      message:
        "Registrazione troppo breve. Leggi la frase per intero e riprova.",
    });
  });
  await page.getByText(/Registrazione troppo breve/).waitFor();
  pass("Start and save failures are shown and leave the page usable");

  // A dictation in progress owns the microphone.
  await page.evaluate(() => {
    const w = window as any;
    w.state = { ...w.state, revision: 2, phase: "recording" };
    w.send("dictation-state", w.state);
  });
  await page.getByText(/la registrazione del profilo riprende/).waitFor();
  assert.equal(
    await card.getByRole("button", { name: "Rifai", exact: true }).isDisabled(),
    true,
  );
  await page.evaluate(() => {
    const w = window as any;
    w.state = { ...w.state, revision: 3, phase: "idle" };
    w.send("dictation-state", w.state);
  });
  await page
    .getByText(/la registrazione del profilo riprende/)
    .waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "Dettagli tecnici" }).click();
  await page
    .getByText("Cartella: /fixture/VoiceFlow/voice-profile", { exact: true })
    .waitFor();
  await page.getByRole("button", { name: "Cancella tutto il profilo" }).click();
  const dialog = page.getByRole("alertdialog");
  await dialog.getByText(/Le frasi da leggere restano/).waitFor();
  await dialog.getByRole("button", { name: "Annulla", exact: true }).click();
  assert.deepEqual(await calls("voice_profile_delete"), []);
  await page.getByRole("button", { name: "Cancella tutto il profilo" }).click();
  await dialog.getByRole("button", { name: "Cancella tutto" }).click();
  await dialog.waitFor({ state: "hidden" });
  await progress.getByText(/0 di 3 frasi registrate/).waitFor();
  await card.getByText("Frase 1 di 3", { exact: true }).waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "Cancella tutto il profilo" })
      .isDisabled(),
    true,
  );
  pass(
    "Dictation blocks profile recording; deleting the profile asks first and resets the progress",
  );
  await page.screenshot({ path: resolve(output, "voice-profile.png") });

  await page.evaluate(() => {
    const w = window as any;
    w.state = { ...w.state, revision: 4, modelReady: false };
    w.send("dictation-state", w.state);
  });
  await page.getByText(/Il modello non è caricato/).waitFor();
  assert.equal(
    await card
      .getByRole("button", { name: "Registra", exact: true })
      .isDisabled(),
    true,
  );
  // The fixture is rebuilt on reload: this flag outlives it.
  await page.evaluate(() => sessionStorage.setItem("unavailable", "1"));
  await page.reload();
  await page.getByText("Nessuna frase da leggere", { exact: true }).waitFor();
  await page.getByText(/prompts\.json/).waitFor();
  assert.equal(await page.getByLabel("Frase da leggere").count(), 0);
  // Nothing on this page can play audio.
  assert.equal(await page.locator("audio, video").count(), 0);
  assert.equal(
    await page.evaluate(
      "window.calls.some(c=>['toggle_recording','stop_recording','pause_recording','cancel_recording'].includes(c.cmd))",
    ),
    false,
  );
  assert.deepEqual(errors, []);
  pass(
    "Unloaded model and missing script are explained; no playback element, dictation command or browser error",
  );
  await writeFile(
    resolve(output, "result.json"),
    JSON.stringify({ checks, errors }, null, 2),
  );
} finally {
  await browser.close();
}
