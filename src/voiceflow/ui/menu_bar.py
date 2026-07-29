from __future__ import annotations

from typing import Any


class MenuBar:
    def __init__(
        self,
        *,
        on_retry_input: Any,
        on_retry_model: Any,
        on_retry_postprocess: Any,
        on_copy_last: Any,
        on_copy_raw: Any,
        on_correct_last: Any,
        on_add_term: Any,
        on_clear_history: Any,
        on_deep_next: Any,
        on_quit: Any,
    ) -> None:
        import objc
        from AppKit import NSMenu, NSMenuItem, NSStatusBar
        from Foundation import NSObject

        class Actions(NSObject):
            retry_input_callback: Any = None
            retry_model_callback: Any = None
            retry_postprocess_callback: Any = None
            copy_last_callback: Any = None
            copy_raw_callback: Any = None
            correct_last_callback: Any = None
            add_term_callback: Any = None
            clear_history_callback: Any = None
            deep_next_callback: Any = None
            quit_callback: Any = None

            @objc.IBAction
            def retryInput_(self, _sender: Any) -> None:
                self.retry_input_callback()

            @objc.IBAction
            def retryModel_(self, _sender: Any) -> None:
                self.retry_model_callback()

            @objc.IBAction
            def retryPostprocess_(self, _sender: Any) -> None:
                self.retry_postprocess_callback()

            @objc.IBAction
            def copyLast_(self, _sender: Any) -> None:
                self.copy_last_callback()

            @objc.IBAction
            def copyRaw_(self, _sender: Any) -> None:
                self.copy_raw_callback()

            @objc.IBAction
            def correctLast_(self, _sender: Any) -> None:
                self.correct_last_callback()

            @objc.IBAction
            def addTerm_(self, _sender: Any) -> None:
                self.add_term_callback()

            @objc.IBAction
            def clearHistory_(self, _sender: Any) -> None:
                self.clear_history_callback()

            @objc.IBAction
            def deepNext_(self, _sender: Any) -> None:
                self.deep_next_callback()

            @objc.IBAction
            def quit_(self, _sender: Any) -> None:
                self.quit_callback()

        self.actions = Actions.alloc().init()
        for name, callback in (
            ("retry_input_callback", on_retry_input),
            ("retry_model_callback", on_retry_model),
            ("retry_postprocess_callback", on_retry_postprocess),
            ("copy_last_callback", on_copy_last),
            ("copy_raw_callback", on_copy_raw),
            ("correct_last_callback", on_correct_last),
            ("add_term_callback", on_add_term),
            ("clear_history_callback", on_clear_history),
            ("deep_next_callback", on_deep_next),
            ("quit_callback", on_quit),
        ):
            setattr(self.actions, name, callback)

        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(-1)
        self.status_item.button().setTitle_("●")
        menu = NSMenu.alloc().init()
        self.status_line = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Inizializzazione…", None, ""
        )
        self.status_line.setEnabled_(False)
        menu.addItem_(self.status_line)
        self.model_line = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Modello: non caricato", None, ""
        )
        self.model_line.setEnabled_(False)
        menu.addItem_(self.model_line)
        self.provider_line = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "ONNX: CPUExecutionProvider", None, ""
        )
        self.provider_line.setEnabled_(False)
        menu.addItem_(self.provider_line)
        self.input_line = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Ingresso: —", None, ""
        )
        self.input_line.setEnabled_(False)
        menu.addItem_(self.input_line)
        self.postprocess_line = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Post-processing: —", None, ""
        )
        self.postprocess_line.setEnabled_(False)
        menu.addItem_(self.postprocess_line)
        self.queue_line = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Coda: 0", None, ""
        )
        self.queue_line.setEnabled_(False)
        menu.addItem_(self.queue_line)
        menu.addItem_(NSMenuItem.separatorItem())
        retry_input = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Riprova permessi / ingresso", "retryInput:", ""
        )
        retry_input.setTarget_(self.actions)
        menu.addItem_(retry_input)
        retry_model = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Retry model loading", "retryModel:", ""
        )
        retry_model.setTarget_(self.actions)
        menu.addItem_(retry_model)
        retry_postprocess = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Riprova post-processing", "retryPostprocess:", ""
        )
        retry_postprocess.setTarget_(self.actions)
        menu.addItem_(retry_postprocess)
        menu.addItem_(NSMenuItem.separatorItem())
        for title, action in (
            ("Copia ultimo testo", "copyLast:"),
            ("Copia transcript originale", "copyRaw:"),
            ("Correggi ultima trascrizione…", "correctLast:"),
            ("Aggiungi termine…", "addTerm:"),
            ("Cancella cronologia", "clearHistory:"),
            ("Pulizia approfondita alla prossima dettatura", "deepNext:"),
        ):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            item.setTarget_(self.actions)
            menu.addItem_(item)
        menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit VoiceBud", "quit:", "q"
        )
        quit_item.setTarget_(self.actions)
        menu.addItem_(quit_item)
        self.status_item.setMenu_(menu)

    def update(
        self,
        *,
        icon: str,
        text: str,
        model_path: str | None,
        provider: str,
        postprocess: str,
        postprocess_state: str,
        postprocess_detail: str | None,
        input_device: str | None,
        queue_depth: int,
    ) -> None:
        self.status_item.button().setTitle_(icon)
        self.status_line.setTitle_(text)
        self.model_line.setTitle_(f"Modello: {model_path or 'non caricato'}")
        self.provider_line.setTitle_(f"ONNX: {provider}")
        self.input_line.setTitle_(f"Ingresso: {input_device or 'non attivo'}")
        detail = f" — {postprocess_detail}" if postprocess_detail else ""
        self.postprocess_line.setTitle_(
            f"Post-processing: {postprocess} ({postprocess_state}){detail}"
        )
        self.queue_line.setTitle_(f"Coda: {queue_depth}")
