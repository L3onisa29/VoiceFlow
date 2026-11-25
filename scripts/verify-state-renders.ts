// Count real component executions in an instrumented build, without a server.
import assert from "node:assert/strict";
import { mkdtemp, readFile, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { build } from "../apps/desktop/node_modules/vite/dist/node/index.js";

const root = resolve(import.meta.dirname, "..");
const outDir = await mkdtemp(resolve(tmpdir(), "voiceflow-render-check-"));
const baseline = process.argv.includes("--baseline");
const tracked = [
  "AppSidebar",
  "SettingsSidebar",
  "SettingsPage",
  "DictationPage",
  "TranscriptPanel",
  "RecordingControls",
  "AudioMeter",
  "DictationStatus",
  "TranscriptionHistory",
];
await build({
  root: resolve(process.env.STATE_SOURCE_ROOT ?? root, "apps/desktop"),
  configFile: resolve(
    process.env.STATE_SOURCE_ROOT ?? root,
    "apps/desktop/vite.config.ts",
  ),
  build: { outDir, emptyOutDir: true },
  plugins: [
    {
      name: "test-render-counts",
      enforce: "pre",
      transform(code, id) {
        // Route components live in code-split virtual modules (`?tsr-split=…`).
        const file = id.split("?")[0];
        if (!file.includes("/src/") || !file.endsWith(".tsx")) return;
        // Insert after the function signature, including typed/destructured props.
        for (const name of tracked) {
          const start = code.indexOf(`function ${name}(`);
          if (start < 0) continue;
          let depth = 1;
          let i = start + `function ${name}(`.length;
          for (; depth; i++) {
            if (code[i] === "(") depth++;
            if (code[i] === ")") depth--;
          }
          const body = code.indexOf("{", i) + 1;
          code =
            code.slice(0, body) +
            `\nglobalThis.__renders[${JSON.stringify(name)}] = (globalThis.__renders[${JSON.stringify(name)}] || 0) + 1;\n` +
            code.slice(body);
        }
        return code;
      },
    },
  ],
});
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.route("https://voiceflow.test/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      body: await readFile(
        resolve(outDir, path === "/" ? "index.html" : path.slice(1)),
      ),
      contentType: path.endsWith(".js")
        ? "text/javascript"
        : path.endsWith(".css")
          ? "text/css"
          : path.endsWith(".woff2")
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
  await page.addInitScript({
    content:
      mocks +
      `
    window.__renders = {}; window.listeners = {}; window.calls = [];
    window.snapshot = {revision:1,phase:'recording',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:3,device:'Fixture',settings:{language:'italian',format:'faithful',cleanup:false},transcript:{confirmed:'Testo stabile',partial:'',audioSeconds:3,inferenceSeconds:0,chunks:1,finished:false}};
    mockIPC(async(cmd,args) => {
      window.calls.push(cmd);
      if(cmd==='plugin:event|listen'){window.listeners[args.event]=args.handler;return args.handler;}
      if(cmd==='plugin:event|unlisten'){delete window.listeners[args.event];return;}
      if(cmd==='get_state') return structuredClone(window.snapshot);
      if(cmd==='get_permissions') return {microphone:true,microphoneStatus:'authorized',accessibility:true};
      if(cmd==='history_page')return {entries:[],nextCursor:null,stats:{totalWords:0,totalSessions:0,totalSeconds:0,wordsPerMinute:0}};
      if(cmd==='analysis_status'||cmd==='scheduler_status') throw new Error('Fixture: settings unavailable');
      return null;
    });
    window.sendSnapshot = next => {window.snapshot = next; const h=window.listeners['dictation-state']; window.__TAURI_INTERNALS__.runCallback(h,{event:'dictation-state',id:h,payload:structuredClone(next)});};
  `,
  });
  await page.goto("https://voiceflow.test/");
  await page.getByText("Testo stabile", { exact: true }).waitFor();
  // Recents share the first history page; with no history the link is hidden.
  await page.getByText("Nessuna dettatura recente", { exact: true }).waitFor();
  assert.equal(
    await page.getByRole("link", { name: "Vedi tutto lo storico" }).count(),
    0,
  );
  // Every tracked component on this page is instrumented (incl. code-split routes).
  const initial = (await page.evaluate(
    () => (window as any).__renders,
  )) as Record<string, number>;
  for (const name of [
    "AppSidebar",
    "DictationPage",
    "TranscriptPanel",
    "RecordingControls",
    "AudioMeter",
    "DictationStatus",
  ])
    assert.ok((initial[name] ?? 0) > 0, `${name} is not instrumented`);
  async function audioBurst() {
    await page.evaluate(() => {
      (window as any).__renders = {};
    });
    for (let n = 1; n <= 30; n++) {
      await page.evaluate((n) => {
        const w = window as any;
        w.sendSnapshot({
          ...w.snapshot,
          revision: w.snapshot.revision + 1,
          level: n / 100,
          transcript: { ...w.snapshot.transcript, audioSeconds: 3 + n / 10 },
        });
      }, n);
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => resolve()),
          ),
      );
    }
    return (await page.evaluate(() => (window as any).__renders)) as Record<
      string,
      number
    >;
  }
  const dictation = await audioBurst();
  if (!baseline) {
    for (const name of [
      "AppSidebar",
      "DictationPage",
      "TranscriptPanel",
      "RecordingControls",
      "DictationStatus",
      "TranscriptionHistory",
    ])
      assert.equal(dictation[name] ?? 0, 0, name);
    assert.equal(dictation.AudioMeter, 30);
  }
  // Level events between text changes move only the meter, and only for the state
  // they were measured in: a late one never follows a transition.
  await page.evaluate(() => {
    const w = window as any;
    w.__renders = {};
    const handler = w.listeners["dictation-level"];
    const send = (revision: number, level: number) =>
      w.__TAURI_INTERNALS__.runCallback(handler, {
        event: "dictation-level",
        id: handler,
        payload: { revision, level },
      });
    send(w.snapshot.revision, 0.05);
    send(w.snapshot.revision - 1, 0.9);
  });
  const meter = page.getByRole("meter", { name: "Livello microfono" });
  await page.waitForFunction(
    () =>
      document
        .querySelector('[role="meter"]')
        ?.getAttribute("aria-valuenow") === "60",
  );
  assert.equal(await meter.getAttribute("aria-valuenow"), "60");
  if (!baseline) {
    const levelOnly = (await page.evaluate(
      () => (window as any).__renders,
    )) as Record<string, number>;
    for (const name of ["TranscriptPanel", "DictationPage", "AppSidebar"])
      assert.equal(levelOnly[name] ?? 0, 0, name);
    assert.equal(levelOnly.AudioMeter, 1);
  }
  await page.evaluate(() => {
    const w = window as any;
    w.sendSnapshot({
      ...w.snapshot,
      revision: 100,
      transcript: { ...w.snapshot.transcript, confirmed: "Testo aggiornato" },
    });
  });
  await page.getByText("Testo aggiornato", { exact: true }).waitFor();
  await page.evaluate(() => {
    const w = window as any;
    w.sendSnapshot({
      ...w.snapshot,
      revision: 2,
      transcript: { ...w.snapshot.transcript, confirmed: "Obsoleto" },
    });
  });
  assert.equal(await page.getByText("Obsoleto", { exact: true }).count(), 0);
  await page.evaluate(() => {
    const w = window as any;
    w.snapshot.revision = 100;
  });
  for (const modelReady of [false, true]) {
    await page.evaluate((modelReady) => {
      const w = window as any;
      w.sendSnapshot({
        ...w.snapshot,
        revision: w.snapshot.revision + 1,
        phase: "loading",
        modelReady,
      });
    }, modelReady);
    await page
      .getByText(
        modelReady ? "Preparazione della dettatura" : "Caricamento del modello",
        { exact: true },
      )
      .waitFor();
    assert.equal(
      await page.getByRole("button", { name: "Preparazione…" }).isDisabled(),
      true,
    );
    assert.equal(
      await page.getByRole("status", { name: "Preparazione in corso" }).count(),
      2,
    );
    await page
      .getByText(
        'Attendi prima di parlare: la registrazione inizia quando lo stato diventa "In ascolto".',
        { exact: true },
      )
      .waitFor();
    assert.equal(
      await page.getByRole("button", { name: "Carica modello" }).count(),
      0,
    );
  }
  await page.evaluate(() => {
    const w = window as any;
    w.sendSnapshot({
      ...w.snapshot,
      revision: w.snapshot.revision + 1,
      phase: "recording",
      modelReady: true,
    });
  });
  await page.getByText("In ascolto", { exact: true }).waitFor();
  assert.equal(
    await page.getByRole("button", { name: "Termina e incolla" }).isDisabled(),
    false,
  );
  // The full history moved to its own page: it must ignore audio updates too.
  await page.getByRole("link", { name: "Storico", exact: true }).click();
  await page
    .getByText("Nessuna trascrizione salvata", { exact: true })
    .waitFor();
  assert.ok(
    (
      (await page.evaluate(() => (window as any).__renders)) as Record<
        string,
        number
      >
    ).TranscriptionHistory > 0,
    "TranscriptionHistory is not instrumented",
  );
  const history = await audioBurst();
  if (!baseline) {
    for (const name of ["AppSidebar", "TranscriptionHistory"])
      assert.equal(history[name] ?? 0, 0, name);
  }
  await page.evaluate(() => {
    (window as any).__renders = {};
  });
  await page.getByRole("link", { name: "Impostazioni", exact: true }).click();
  await page
    .getByRole("heading", { name: "Impostazioni", exact: true })
    .waitFor();
  // The sidebar renders before the code-split settings page: wait for both.
  await page.locator("#text-settings").waitFor({ state: "attached" });
  for (const name of ["SettingsSidebar", "SettingsPage"])
    assert.ok(
      (
        (await page.evaluate(() => (window as any).__renders)) as Record<
          string,
          number
        >
      )[name] > 0,
      `${name} is not instrumented`,
    );
  const settings = await audioBurst();
  if (!baseline) {
    assert.equal(settings.AppSidebar ?? 0, 0);
    assert.equal(settings.SettingsSidebar ?? 0, 0);
    assert.equal(settings.SettingsPage ?? 0, 0);
  }
  const connections = await page.evaluate(
    () => (window as any).calls.filter((c: string) => c === "get_state").length,
  );
  assert.equal(connections, 1, "Navigation must not reconnect the session");
  assert.deepEqual(errors, []);
  const result = { dictation, history, settings, connections };
  await mkdir(resolve(root, "artifacts/state"), { recursive: true });
  await writeFile(
    resolve(
      root,
      `artifacts/state/renders-${baseline ? "before" : "after"}.json`,
    ),
    JSON.stringify(result, null, 2) + "\n",
  );
  console.log(JSON.stringify(result, null, 2));
} finally {
  await browser.close();
  await rm(outDir, { recursive: true, force: true });
}
