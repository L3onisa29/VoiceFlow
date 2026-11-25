// Production UI verification of the panels over other applications: the live
// transcript above the HUD and the recent dictations picker. Tauri IPC fixtures only;
// no HTTP listener, no audio. Screenshots go to artifacts/overlays.
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const modulePath = process.env.PLAYWRIGHT_MODULE;
const { chromium } = await import(
  modulePath ? pathToFileURL(modulePath).href : "playwright"
);
const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "artifacts/overlays");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const mocks = (
  await readFile(
    resolve(root, "apps/desktop/node_modules/@tauri-apps/api/mocks.js"),
    "utf8",
  )
).replace(/export\s*\{[^}]*\};?/g, "");

const fixtures = `
window.calls = [];
window.state = {revision:1,session:1,recoverable:false,phase:'recording',modelReady:true,modelPresent:true,finalText:'',message:null,historyError:null,level:0,elapsedSeconds:14,device:'Fixture',settings:{language:'italian',format:'faithful',cleanup:false},transcript:{confirmed:"Ciao Marco, volevo aggiornarti sul progetto voice flow. Abbiamo finito l'integrazione con tauri ",partial:'la settimana',audioSeconds:14,inferenceSeconds:1,chunks:9,finished:false}};
const now = Date.now();
const produced = "Ciao Marco, volevo aggiornarti sul progetto voice flow. Abbiamo finito l'integrazione con tauri la settimana scorsa e adesso il decoder gira gira in locale. Il prossimo passo è la revisione dei suggerimenti del dizionario.";
const corrected = "Ciao Marco, volevo aggiornarti sul progetto VoiceFlow. Abbiamo finito l'integrazione con tauri la settimana scorsa e adesso il decoder gira gira in locale. Il prossimo passo è la revisione dei suggerimenti del dizionario.";
window.recent = [
  {id:3,createdAt:now-120000,text:corrected,producedText:produced,durationSeconds:42,pasted:true,interrupted:false,destinationApp:'Mail'},
  {id:2,createdAt:now-1080000,text:'Ricordami di controllare i permessi del microfono prima della demo di giovedì.',producedText:'Ricordami di controllare i permessi del microfono prima della demo di giovedì.',durationSeconds:7,pasted:false,interrupted:false,destinationApp:'Slack'},
  {id:1,createdAt:now-3600000,text:'Ok per la call, propongo le 15 così abbiamo tempo di',producedText:'Ok per la call, propongo le 15 così abbiamo tempo di',durationSeconds:5,pasted:null,interrupted:true,destinationApp:null},
];
window.detail = {entry:{id:3,createdAt:now-120000,text:corrected,wordCount:30,durationSeconds:42,language:'it',uncertainWords:0,unknownRemoved:0},originalAsr:null,producedText:produced,processing:null,editVersion:1,currentRevision:1,revisions:[{id:1,createdAt:now-100000,beforeText:produced,text:corrected,reverted:false,category:'recognition',origin:'live',segment:0}]};
mockIPC(async (cmd,args) => {
  window.calls.push({cmd,args});
  switch (cmd) {
    case 'get_state': return structuredClone(window.state);
    case 'live_edit': case 'overlay_fit': case 'overlay_hide': case 'overlay_release': case 'history_copy': return;
    case 'recents_insert': return 'Inserito nel campo attivo';
    case 'history_recent': return structuredClone(window.recent.filter(e => e.text.toLowerCase().includes(args.query.search.toLowerCase())));
    case 'history_detail': return structuredClone(window.detail);
    case 'history_save_changes': {
      const d = window.detail;
      if (args.request.expectedVersion !== d.editVersion) throw new Error('Versione cambiata');
      let before = d.entry.text;
      for (const step of args.request.steps) {
        const id = d.revisions.length + 1;
        d.revisions.unshift({id,createdAt:now,beforeText:before,text:step.text,reverted:false,category:step.category,origin:args.request.origin,segment:step.segment});
        before = step.text;
        d.currentRevision = id;
      }
      d.entry.text = before; d.editVersion++;
      return structuredClone(d);
    }
    case 'history_remember': window.detail.revisions.find(r => r.id === args.request.revisionId).category = args.request.category; return structuredClone(window.detail);
    case 'history_forget': window.detail.revisions.find(r => r.id === args.request.revisionId).category = null; return structuredClone(window.detail);
  }
  if (cmd.startsWith('plugin:event|')) return;
  throw new Error('Unexpected IPC ' + cmd);
}, {shouldMockEvents:true});
window.fire = (event, payload) => window.__TAURI_INTERNALS__.invoke('plugin:event|emit', {event, payload});
`;

async function open(page: string, width: number, height: number) {
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 2,
  });
  await context.route("https://voiceflow.test/**", async (route) => {
    const file = new URL(route.request().url()).pathname.slice(1);
    await route.fulfill({
      body: await readFile(resolve(root, "apps/desktop/dist", file)),
      contentType: file.endsWith(".js")
        ? "text/javascript"
        : file.endsWith(".css")
          ? "text/css"
          : "text/html",
    });
  });
  await context.addInitScript({ content: mocks + fixtures });
  const tab = await context.newPage();
  const errors: string[] = [];
  tab.on("pageerror", (error) => errors.push(String(error)));
  tab.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await tab.goto(`https://voiceflow.test/${page}`);
  // The window is transparent; paint a desktop behind it for the screenshots.
  await tab.addStyleTag({
    content:
      "html:root{background:linear-gradient(160deg,#2b2f3a,#1b1d24 60%,#15161b)!important}",
  });
  return { tab, errors, context };
}

const calls = (tab: import("playwright").Page, name: string) =>
  tab.evaluate(
    (cmd) =>
      (window as unknown as { calls: { cmd: string; args: unknown }[] }).calls
        .filter((call) => call.cmd === cmd)
        .map((call) => call.args),
    name,
  );

/** Select `text` inside the element matching `selector`, as a user would drag over it. */
async function select(
  tab: import("playwright").Page,
  selector: string,
  text: string,
) {
  await tab.evaluate(
    ([css, wanted]) => {
      const element = document.querySelector(css) as HTMLElement;
      element.focus();
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const at = node.textContent!.indexOf(wanted);
        if (at >= 0) {
          window
            .getSelection()!
            .setBaseAndExtent(node, at, node, at + wanted.length);
          return;
        }
      }
      throw new Error("Text not found: " + wanted);
    },
    [selector, text] as const,
  );
}

const results: string[] = [];
const pass = (name: string) => {
  results.push(name);
  console.log("PASS", name);
};

try {
  // Live panel: free correction while new words keep arriving.
  {
    const { tab, errors, context } = await open("live.html", 460, 300);
    const editor = tab.getByRole("textbox", { name: "Testo della dettatura" });
    await editor.waitFor();
    await tab.waitForTimeout(400);
    const fits = (await calls(tab, "overlay_fit")) as {
      request: { window: string };
    }[];
    assert.ok(fits.some((call) => call.request.window === "live"));
    pass("live panel sizes itself above the HUD once recording starts");
    await tab.screenshot({ path: resolve(output, "live-recording.png") });

    await select(tab, '[role="textbox"]', "voice flow");
    await tab.keyboard.type("VoiceFlow");
    await tab.waitForTimeout(100);
    const edits = (await calls(tab, "live_edit")) as {
      edit: {
        session: number;
        base: string;
        text: string;
        steps: { segment: number; category: string | null }[];
      };
    }[];
    const last = edits.at(-1)!.edit;
    assert.equal(last.session, 1);
    assert.match(last.text, /progetto VoiceFlow\./);
    assert.deepEqual(
      last.steps.map((step) => [step.segment, step.category]),
      [[0, "recognition"]],
    );
    assert.equal(
      await tab.locator('[data-diff="add"]').first().textContent(),
      "V",
    );
    pass(
      "an edit is sent with its sentence and lesson, only changed letters are green",
    );

    await tab.evaluate(() => {
      const w = window as unknown as {
        state: {
          revision: number;
          transcript: { confirmed: string; partial: string };
        };
        fire: (event: string, payload: unknown) => void;
      };
      w.state.revision = 2;
      w.state.transcript.confirmed +=
        "la settimana scorsa e adesso il decoder gira gira in locale. ";
      w.state.transcript.partial = "Il prossimo";
      w.fire("dictation-state", structuredClone(w.state));
    });
    await tab.waitForTimeout(100);
    const text = await editor.textContent();
    assert.match(text!, /progetto VoiceFlow\. Abbiamo/);
    assert.match(text!, /gira gira in locale\. $/);
    assert.equal(
      await tab.evaluate(() => window.getSelection()?.rangeCount),
      1,
    );
    pass("new confirmed words are appended after the user's version");

    await select(tab, '[role="textbox"]', "gira ");
    await tab.keyboard.press("Backspace");
    await tab.waitForTimeout(100);
    const repetition = (
      (await calls(tab, "live_edit")) as {
        edit: { steps: { segment: number; category: string | null }[] };
      }[]
    ).at(-1)!.edit;
    assert.deepEqual(
      repetition.steps.map((step) => [step.segment, step.category]),
      [
        [0, "recognition"],
        [1, "cleanup"],
      ],
    );
    // The space dropped from "voice flow" is the other, whitespace, trace.
    assert.equal(
      await tab.locator('[data-diff="del"]:not([data-space])').count(),
      1,
    );
    assert.equal(
      await tab
        .locator('[data-diff="del"]:not([data-space])')
        .getAttribute("data-t"),
      "gira",
    );
    pass(
      "a removed repetition leaves a red trace and becomes a cleanup lesson",
    );
    await tab.screenshot({ path: resolve(output, "live-corrected.png") });

    await tab.keyboard.press("Escape");
    assert.ok((await calls(tab, "overlay_release")).length > 0);
    pass("Escape gives the keyboard back to the application");

    await tab.evaluate(() => {
      const w = window as unknown as {
        state: {
          revision: number;
          phase: string;
          recoverable: boolean;
          message: string;
        };
        fire: (event: string, payload: unknown) => void;
      };
      w.state.revision = 3;
      w.state.phase = "error";
      w.state.recoverable = true;
      w.state.message = "Microfono non disponibile: audio incompleto.";
      w.fire("dictation-state", structuredClone(w.state));
    });
    await tab.getByText("Testo conservato").waitFor();
    assert.equal(await tab.getByText("Microfono non disponibile").count(), 0);
    pass(
      "on error the panel speaks about the text, never repeats the pill's message",
    );
    await tab.screenshot({ path: resolve(output, "live-error.png") });
    assert.deepEqual(errors, []);
    await context.close();
  }

  // Recent dictations and the correction screen.
  {
    const { tab, errors, context } = await open("recents.html", 600, 620);
    await tab.evaluate(() =>
      (window as unknown as { fire: (e: string) => void }).fire("recents-open"),
    );
    await tab.getByRole("listbox", { name: "Trascrizioni recenti" }).waitFor();
    assert.equal(await tab.getByRole("option").count(), 3);
    await tab.getByText("1 correzione").waitFor();
    await tab.getByText("Interrotta").waitFor();
    pass("recents list delivery, destination, interruptions and corrections");
    await tab.screenshot({ path: resolve(output, "recents-list.png") });

    await tab.keyboard.type("permessi");
    await tab.waitForTimeout(50);
    assert.equal(await tab.getByRole("option").count(), 1);
    await tab.keyboard.press("Enter");
    await tab.waitForTimeout(50);
    assert.deepEqual(await calls(tab, "recents_insert"), [
      {
        text: "Ricordami di controllare i permessi del microfono prima della demo di giovedì.",
      },
    ]);
    pass("typing filters and Enter inserts the selected dictation");

    await tab.evaluate(() =>
      (window as unknown as { fire: (e: string) => void }).fire("recents-open"),
    );
    await tab.getByRole("option").first().waitFor();
    await tab.keyboard.press("Meta+e");
    const correction = tab.getByRole("textbox", {
      name: "Testo da correggere",
    });
    await correction.waitFor();
    await tab.getByText("Dettatura in Mail").waitFor();
    await tab.getByRole("button", { name: "Indietro" }).waitFor();
    pass("⌘E opens the correction screen with Indietro while nothing changed");
    await tab.screenshot({ path: resolve(output, "correct-closed.png") });

    await tab.getByRole("button", { name: /Modifiche \(1\)/ }).click();
    await tab.getByText("salvata · durante la registrazione").waitFor();
    pass("saved live corrections show their origin and lesson");

    await select(tab, '[role="textbox"]', "tauri");
    await tab.keyboard.type("Tauri");
    await select(tab, '[role="textbox"]', "gira ");
    await tab.keyboard.press("Backspace");
    await tab.getByRole("button", { name: "Annulla" }).waitFor();
    await tab.getByText("· 1 da salvare").waitFor();
    await tab.waitForTimeout(350);
    await tab.screenshot({ path: resolve(output, "correct-open.png") });

    await tab.locator("body").click({ position: { x: 5, y: 5 } });
    await tab.keyboard.press("Meta+s");
    await tab.waitForTimeout(100);
    const saves = (await calls(tab, "history_save_changes")) as {
      request: {
        origin: string;
        expectedVersion: number;
        steps: { segment: number; category: string | null; restore: boolean }[];
      };
    }[];
    assert.equal(saves.length, 1);
    assert.equal(saves[0].request.origin, "review");
    assert.deepEqual(
      saves[0].request.steps.map((step) => [step.segment, step.category]),
      [[1, "cleanup"]],
    );
    pass("saving creates one review revision per changed sentence");

    // Restoring the live correction forgets its lesson.
    await tab.getByRole("button", { name: "Ripristina la frase 1" }).click();
    await tab.keyboard.press("Meta+s");
    await tab.waitForTimeout(100);
    const restore = (
      (await calls(tab, "history_save_changes")) as {
        request: {
          steps: { segment: number; restore: boolean; category: unknown }[];
        };
      }[]
    ).at(-1)!.request.steps;
    assert.deepEqual(
      restore.map(({ segment, restore, category }) => [
        segment,
        restore,
        category,
      ]),
      [[0, true, null]],
    );
    pass("restoring a sentence is saved as a restore without a lesson");
    await tab.screenshot({ path: resolve(output, "correct-saved.png") });

    await tab.keyboard.press("Meta+Enter");
    await tab.waitForTimeout(100);
    assert.equal((await calls(tab, "recents_insert")).length, 2);
    pass("⌘↵ inserts the corrected text");
    assert.deepEqual(errors, []);
    await context.close();
  }
} finally {
  await browser.close();
  await writeFile(
    resolve(output, "result.json"),
    JSON.stringify({ passed: results }, null, 2) + "\n",
  );
}
