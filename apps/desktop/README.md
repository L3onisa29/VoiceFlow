# VoiceFlow desktop

React and TypeScript UI embedded in a Tauri 2 macOS app. Rust owns microphone
capture, NVIDIA streaming inference, permissions, global shortcuts and delivery.

`src/routes/index.tsx` defines both the route and its `DictationPage` component.
The page owns the single `useDictation` subscription and composes the components
in `src/components/dictation` inside `src/components/layout/app-shell.tsx`.
Global styles live in `src/styles/app.css` and are imported by `src/main.tsx`.

Run build and verification commands from the repository root. See
[the desktop documentation](../../docs/desktop.md).
