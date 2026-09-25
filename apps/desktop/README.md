# VoiceFlow desktop

React and TypeScript UI embedded in a Tauri 2 macOS app. Rust owns microphone
capture, NVIDIA streaming inference, permissions, global shortcuts and delivery.

`src/routes/__root.tsx` owns the shared shell and `DictationProvider`. The provider
keeps one native event subscription and permission poller alive across navigation.
`src/routes/index.tsx`, `src/routes/dictionary/index.tsx` and
`src/routes/settings/index.tsx` define the three pages. Navigation entries live in
`src/components/layout/navigation.ts`; routes are registered in `src/router.ts`.
Global styles live in `src/styles/app.css` and are imported by `src/main.tsx`.

The local dictionary uses SQLite through `src-tauri/src/dictionary.rs`, independent
of the dictation worker. Saved terms do not yet affect transcription. See
[dictionary and settings](../../docs/dictionary.md) for storage and behavior.

Run build and verification commands from the repository root. See
[the desktop documentation](../../docs/desktop.md).
