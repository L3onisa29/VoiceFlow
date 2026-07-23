from __future__ import annotations

from typing import Any


class MenuBar:
    def __init__(
        self,
        *,
        on_retry_input: Any,
        on_retry_model: Any,
        on_retry_postprocess: Any,
        on_quit: Any,
    ) -> None:
        import objc
        from AppKit import NSMenu, NSMenuItem, NSStatusBar
        from Foundation import NSObject

        class Actions(NSObject):
            retry_input_callback: Any = None
            retry_model_callback: Any = None
            retry_postprocess_callback: Any = None
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
            def quit_(self, _sender: Any) -> None:
                self.quit_callback()

        self.actions = Actions.alloc().init()
        self.actions.retry_input_callback = on_retry_input
        self.actions.retry_model_callback = on_retry_model
        self.actions.retry_postprocess_callback = on_retry_postprocess
        self.actions.quit_callback = on_quit

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
