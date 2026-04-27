// Native Windows notifications via Tauri plugin
// Falls back to Web Notification API if Tauri is not available

let tauriNotification: any = null;
let initialized = false;

async function init() {
  if (initialized) return;
  initialized = true;
  try {
    const mod = await import("@tauri-apps/plugin-notification");
    // Check permission
    let perm = await mod.isPermissionGranted();
    if (!perm) {
      const result = await mod.requestPermission();
      perm = result === "granted";
    }
    if (perm) {
      tauriNotification = mod;
    }
  } catch {
    // Not in Tauri environment — will use Web API fallback
  }
}

// Initialize on first import
init();

export async function sendNotification(title: string, body: string) {
  // Try Tauri native notification first (Windows toast)
  if (tauriNotification) {
    try {
      await tauriNotification.sendNotification({ title, body });
      return;
    } catch {}
  }

  // Fallback: Web Notification API
  if ("Notification" in window) {
    if (Notification.permission === "default") {
      await Notification.requestPermission();
    }
    if (Notification.permission === "granted") {
      new Notification(title, { body });
    }
  }
}