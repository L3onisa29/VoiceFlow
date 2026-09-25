pub mod analysis;
mod capture;
mod dictionary;
mod history;
mod hud;
mod output_audio;
mod platform;
mod service;
mod settings;
pub mod suggestions;
use service::{Command, Service, Snapshot};
use tauri::{
    Manager, State,
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};
use tauri_plugin_opener::OpenerExt;

#[tauri::command]
fn get_state(service: State<'_, Service>) -> Snapshot {
    service.snapshot()
}
#[tauri::command]
fn get_permissions() -> platform::Permissions {
    platform::permissions()
}
#[tauri::command]
fn request_microphone() {
    platform::request_microphone();
}
#[tauri::command]
fn open_privacy(app: tauri::AppHandle, section: String) -> Result<(), String> {
    let pane = match section.as_str() {
        "microphone" => "Privacy_Microphone",
        "accessibility" => "Privacy_Accessibility",
        _ => return Err("Impostazione non valida".into()),
    };
    app.opener()
        .open_url(
            format!("x-apple.systempreferences:com.apple.preference.security?{pane}"),
            None::<&str>,
        )
        .map_err(|error| error.to_string())
}
#[tauri::command]
fn load_model(service: State<'_, Service>) -> Result<(), String> {
    service.send(Command::Load).map_err(|e| e.to_string())
}
#[tauri::command]
fn toggle_recording(app: tauri::AppHandle) -> Result<(), String> {
    send_from_controls(&app, Command::Toggle)
}
#[tauri::command]
fn stop_recording(app: tauri::AppHandle) -> Result<(), String> {
    send_from_controls(&app, Command::Stop)
}
#[tauri::command]
fn pause_recording(service: State<'_, Service>) -> Result<(), String> {
    service.send(Command::Pause).map_err(|e| e.to_string())
}
fn send_from_controls(app: &tauri::AppHandle, command: Command) -> Result<(), String> {
    let handle = app.clone();
    app.run_on_main_thread(move || {
        platform::hide_if_frontmost(objc2_foundation::MainThreadMarker::new().unwrap());
        if let Err(error) = handle.state::<Service>().send(command) {
            eprintln!("{error}");
        }
    })
    .map_err(|e| e.to_string())
}
#[tauri::command]
fn cancel_recording(service: State<'_, Service>) -> Result<(), String> {
    service.send(Command::Cancel).map_err(|e| e.to_string())
}
#[tauri::command]
fn save_settings(service: State<'_, Service>, settings: settings::Settings) -> Result<(), String> {
    service
        .send(Command::Settings(settings))
        .map_err(|e| e.to_string())
}
#[tauri::command]
fn copy_transcript(service: State<'_, Service>) -> Result<(), String> {
    let snapshot = service.snapshot();
    let text = if snapshot.final_text.is_empty() {
        snapshot.transcript.text()
    } else {
        snapshot.final_text
    };
    if text.is_empty() {
        return Err("Nessun testo da copiare".into());
    }
    platform::copy_text(&text).map_err(|e| e.to_string())
}
fn show(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| show(app)))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            get_state,
            get_permissions,
            request_microphone,
            open_privacy,
            load_model,
            toggle_recording,
            stop_recording,
            pause_recording,
            cancel_recording,
            save_settings,
            copy_transcript,
            dictionary::dictionary_snapshot,
            dictionary::dictionary_select_groups,
            dictionary::dictionary_save_term,
            dictionary::dictionary_save_group,
            dictionary::dictionary_delete_term,
            dictionary::dictionary_delete_group,
            history::history_page,
            history::history_copy,
            history::history_delete,
            history::history_detail,
            history::history_edit,
            history::history_undo,
            suggestions::suggestions_snapshot,
            suggestions::suggestions_review,
            analysis::analysis_status,
            analysis::analysis_configure,
            analysis::analysis_exclude,
            analysis::analysis_excluded,
            history::history_remember
        ])
        .setup(|app| {
            let data = app
                .path()
                .home_dir()?
                .join("Library/Application Support/VoiceFlow");
            app.manage(dictionary::DictionaryStore::new(
                data.join("dictionary.sqlite3"),
            ));
            let history = history::HistoryStore::new(data.join("history.sqlite3"));
            app.manage(history.clone());
            app.manage(analysis::AnalysisStore::new(
                history.clone(),
                app.state::<dictionary::DictionaryStore>().inner().clone(),
            ));
            app.manage(suggestions::SuggestionStore::new(
                history.clone(),
                app.state::<dictionary::DictionaryStore>().inner().clone(),
            ));
            let service = Service::new(
                app.handle().clone(),
                data.join("models/nemotron-3.5-streaming-int8"),
                data.join("desktop-settings.json"),
                history,
            )?;
            app.manage(service.clone());
            let toggle: Shortcut = "Alt+Space".parse()?;
            app.global_shortcut().on_shortcut(toggle, |app, _, event| {
                if event.state == ShortcutState::Pressed {
                    let _ = send_from_controls(app, Command::Toggle);
                }
            })?;
            let open = MenuItem::with_id(app, "open", "Apri VoiceFlow", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Esci", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &quit])?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("VoiceFlow · ⌥ Spazio")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show(app),
                    "quit" => {
                        let _ = app.state::<Service>().send(Command::Quit);
                    }
                    _ => {}
                })
                .build(app)?;
            if service.snapshot().model_present {
                service.send(Command::Load)?;
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("VoiceFlow could not start")
        .run(|app, event| {
            if let tauri::RunEvent::ExitRequested {
                code: None, api, ..
            } = event
            {
                // Native Quit must release capture too. The worker then calls
                // app.exit(0), whose explicit exit code is allowed through.
                api.prevent_exit();
                if let Err(error) = app.state::<Service>().send(Command::Quit) {
                    eprintln!("Chiusura di VoiceFlow non riuscita: {error}");
                }
            }
        });
}
