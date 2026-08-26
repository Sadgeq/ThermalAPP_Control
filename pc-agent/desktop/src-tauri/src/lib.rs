mod sidecar;

use sidecar::{agent_running, start_agent_with_session, stop_agent, AgentProcess};
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, WindowEvent,
};

// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_oauth::init())
        // Persists window position/size/maximized across launches.
        .plugin(tauri_plugin_window_state::Builder::default().build())
        // Auto-update plumbing. Inert until tauri.conf.json gains a
        // signed endpoint + pubkey — see Phase 4 in MANUAL_FOLLOWUPS.md.
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(AgentProcess::new())
        .setup(|app| {
            // ---- System tray ----
            // Closing the main window now hides it to the tray instead of
            // exiting; the agent keeps running, monitoring temps and
            // syncing to mobile. The user picks "Quit ThermalControl"
            // from the tray menu when they actually want to stop the agent.
            let show_i = MenuItem::with_id(app, "show", "Show ThermalControl", true, None::<&str>)?;
            let hide_i = MenuItem::with_id(app, "hide", "Hide", true, None::<&str>)?;
            let sep    = PredefinedMenuItem::separator(app)?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit ThermalControl", true, None::<&str>)?;
            let tray_menu = Menu::with_items(app, &[&show_i, &hide_i, &sep, &quit_i])?;

            let _tray = TrayIconBuilder::with_id("main-tray")
                .icon(app.default_window_icon().expect("default window icon").clone())
                .tooltip("ThermalControl")
                .menu(&tray_menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "hide" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.hide();
                        }
                    }
                    "quit" => {
                        // app.exit() bypasses the close-to-tray override
                        // because that override prevents close on the
                        // window — exit() shuts the whole app down.
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    // Left-click toggles window visibility, matching the
                    // muscle memory most Windows tray apps use.
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(w) = app.get_webview_window("main") {
                            let visible = w.is_visible().unwrap_or(false);
                            if visible {
                                let _ = w.hide();
                            } else {
                                let _ = w.show();
                                let _ = w.set_focus();
                            }
                        }
                    }
                })
                .build(app)?;

            // ---- Window close → hide to tray ----
            // Intercept the X button. The user can still genuinely exit
            // via the tray "Quit" menu item, which calls app.exit() and
            // bypasses this handler.
            if let Some(main_window) = app.get_webview_window("main") {
                let app_handle = app.handle().clone();
                main_window.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        if let Some(w) = app_handle.get_webview_window("main") {
                            let _ = w.hide();
                        }
                    }
                });
            }

            // ---- Sidecar (no-op in dev) ----
            // The agent is launched lazily by AuthContext after the user
            // signs in (start_agent_with_session). We deliberately don't
            // spawn here at setup time, so a fresh install with no Google
            // session yet doesn't try to register an unauthenticated agent.

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            greet,
            start_agent_with_session,
            stop_agent,
            agent_running,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            // Kill the sidecar when the app actually exits (tray "Quit"),
            // not when the user just closes the window — that path is now
            // intercepted above and hides the window instead.
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app.try_state::<AgentProcess>() {
                    state.kill();
                }
            }
        });
}
