from __future__ import annotations


def edit_correction(raw_text: str, final_text: str) -> str | None:
    from AppKit import (
        NSAlert,
        NSAlertFirstButtonReturn,
        NSMakeRect,
        NSScrollView,
        NSTextField,
        NSTextView,
        NSView,
    )

    alert = NSAlert.alloc().init()
    alert.setMessageText_("Correggi ultima trascrizione")
    alert.setInformativeText_("Originale e risultato sono in sola lettura.")
    alert.addButtonWithTitle_("Salva")
    alert.addButtonWithTitle_("Annulla")
    view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 520, 330))

    def add_text(title: str, value: str, y: int, *, editable: bool) -> NSTextView:
        label = NSTextField.labelWithString_(title)
        label.setFrame_(NSMakeRect(0, y + 82, 520, 18))
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, y, 520, 78))
        text_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 520, 78))
        text_view.setString_(value)
        text_view.setEditable_(editable)
        scroll.setDocumentView_(text_view)
        scroll.setHasVerticalScroller_(True)
        view.addSubview_(label)
        view.addSubview_(scroll)
        return text_view

    add_text("Originale (sola lettura)", raw_text, 220, editable=False)
    add_text("Risultato (sola lettura)", final_text, 110, editable=False)
    editor = add_text("Correzione", final_text, 0, editable=True)
    alert.setAccessoryView_(view)
    if alert.runModal() != NSAlertFirstButtonReturn:
        return None
    return str(editor.string()).strip()


def prompt_term() -> tuple[str, str] | None:
    from AppKit import NSAlert, NSAlertFirstButtonReturn, NSMakeRect, NSTextField, NSView

    alert = NSAlert.alloc().init()
    alert.setMessageText_("Aggiungi termine")
    alert.setInformativeText_("Inserisci la forma pronunciata e quella da scrivere.")
    alert.addButtonWithTitle_("Continua")
    alert.addButtonWithTitle_("Annulla")
    view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 70))
    spoken = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 40, 420, 24))
    spoken.setPlaceholderString_("Forma pronunciata")
    written = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 5, 420, 24))
    written.setPlaceholderString_("Forma scritta")
    view.addSubview_(spoken)
    view.addSubview_(written)
    alert.setAccessoryView_(view)
    if alert.runModal() != NSAlertFirstButtonReturn:
        return None
    values = str(spoken.stringValue()).strip(), str(written.stringValue()).strip()
    return values if all(values) else None


def confirm(message: str, detail: str) -> bool:
    from AppKit import NSAlert, NSAlertFirstButtonReturn

    alert = NSAlert.alloc().init()
    alert.setMessageText_(message)
    alert.setInformativeText_(detail)
    alert.addButtonWithTitle_("Conferma")
    alert.addButtonWithTitle_("Annulla")
    return alert.runModal() == NSAlertFirstButtonReturn
