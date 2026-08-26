// Sidecar lifecycle for the bundled Python agent
// ===============================================
// In production builds, the PyInstaller-bundled agent ships inside the app's
// resource directory at `agent/agent.exe`. We spawn it AFTER the React UI
// signs the user into Supabase — so the agent receives a fresh access /
// refresh token via env vars and can register the device under the right
// user without any PIN dance.
//
// In dev mode (`tauri dev`), spawn calls are no-ops — the developer runs
// `python Backend/agent.py` themselves in another terminal.

use std::process::Child;
use std::sync::Mutex;

use tauri::{AppHandle, Manager};

/// Wraps the spawned child process. Stored in Tauri app state.
pub struct AgentProcess(pub Mutex<Option<Child>>);

impl AgentProcess {
    pub fn new() -> Self {
        Self(Mutex::new(None))
    }

    pub fn is_running(&self) -> bool {
        self.0
            .lock()
            .map(|guard| guard.is_some())
            .unwrap_or(false)
    }

    pub fn set(&self, child: Child) {
        if let Ok(mut guard) = self.0.lock() {
            *guard = Some(child);
        }
    }

    pub fn kill(&self) {
        if let Ok(mut guard) = self.0.lock() {
            if let Some(mut child) = guard.take() {
                // Best-effort. If the process has already exited, kill()
                // returns InvalidInput on Windows — we don't care.
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

/// Internal: actually launch agent.exe with optional Supabase tokens in env.
///
/// Skipped in dev builds. If a previous child is still running, kill it
/// first so a sign-out → sign-in switch produces a clean session under the
/// new user.
fn spawn(
    app: &AppHandle,
    access_token: Option<String>,
    refresh_token: Option<String>,
) -> Result<(), String> {
    if cfg!(debug_assertions) {
        eprintln!("Sidecar: dev build — skipping auto-spawn (run python Backend/agent.py manually)");
        return Ok(());
    }

    // Replace any existing child cleanly. The agent's single-instance lock
    // (port-bind probe) would otherwise reject a second spawn.
    if let Some(state) = app.try_state::<AgentProcess>() {
        if state.is_running() {
            eprintln!("Sidecar: replacing existing agent process");
            state.kill();
        }
    }

    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|e| format!("resource_dir() failed: {e}"))?;

    let agent_exe = resource_dir.join("agent").join("agent.exe");
    if !agent_exe.exists() {
        return Err(format!(
            "Bundled agent not found at {}. Run pc-agent/build_agent.bat before tauri build.",
            agent_exe.display()
        ));
    }

    eprintln!("Sidecar: spawning {}", agent_exe.display());

    let mut command = std::process::Command::new(&agent_exe);
    if let Some(at) = access_token.as_ref() {
        command.env("TAURI_ACCESS_TOKEN", at);
    }
    if let Some(rt) = refresh_token.as_ref() {
        command.env("TAURI_REFRESH_TOKEN", rt);
    }

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    let child = command
        .spawn()
        .map_err(|e| format!("Failed to spawn agent: {e}"))?;

    eprintln!("Sidecar: agent spawned (pid {})", child.id());

    if let Some(state) = app.try_state::<AgentProcess>() {
        state.set(child);
    }
    Ok(())
}

/// Tauri command: start the agent with a freshly-signed-in Supabase session.
/// Called by the React app from AuthContext once the user has a session.
#[tauri::command]
pub fn start_agent_with_session(
    app: AppHandle,
    access_token: String,
    refresh_token: String,
) -> Result<(), String> {
    if access_token.is_empty() || refresh_token.is_empty() {
        return Err("access_token and refresh_token are required".into());
    }
    spawn(&app, Some(access_token), Some(refresh_token))
}

/// Tauri command: stop the running agent (e.g. on sign-out).
#[tauri::command]
pub fn stop_agent(app: AppHandle) -> Result<(), String> {
    if let Some(state) = app.try_state::<AgentProcess>() {
        state.kill();
    }
    Ok(())
}

/// Tauri command: best-effort report whether the agent is currently spawned
/// by us. Doesn't probe network state — that's what /api/status is for.
#[tauri::command]
pub fn agent_running(app: AppHandle) -> bool {
    app.try_state::<AgentProcess>()
        .map(|s| s.is_running())
        .unwrap_or(false)
}
