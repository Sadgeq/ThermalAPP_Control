import { useState, useEffect } from "react";
import { useTheme } from "../lib/ThemeContext";
import { useAuth } from "../lib/AuthContext";
import { fetchStatus, StatusData } from "../lib/api";
import Card from "../components/Card";

export const SETTINGS_KEY = "tc-settings";

export type AppSettings = {
  cpuWarnThreshold: number;
  cpuCritThreshold: number;
  gpuWarnThreshold: number;
  gpuCritThreshold: number;
  enableNotifications: boolean;
  enableCloudSync: boolean;
};

export const DEFAULT_SETTINGS: AppSettings = {
  cpuWarnThreshold: 75, cpuCritThreshold: 90,
  gpuWarnThreshold: 75, gpuCritThreshold: 90,
  enableNotifications: true, enableCloudSync: true,
};

export function loadSettings(): AppSettings {
  try { const raw = localStorage.getItem(SETTINGS_KEY); return raw ? { ...DEFAULT_SETTINGS, ...JSON.parse(raw) } : DEFAULT_SETTINGS; }
  catch { return DEFAULT_SETTINGS; }
}
function saveSettings(s: AppSettings) { try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); } catch {} }

async function syncThresholdsToAgent(s: AppSettings) {
  // The agent stores a single threshold per metric (the critical level that
  // fires alerts). Warning thresholds are UI-only — they tint the dashboard
  // gauges but don't generate notifications. Push the critical values to the
  // agent's /api/alerts/threshold endpoint, one POST per metric.
  const post = (metric: "cpu_temp" | "gpu_temp", threshold: number) =>
    fetch("http://127.0.0.1:8420/api/alerts/threshold", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ metric, threshold }),
    }).catch(() => {});
  await Promise.all([
    post("cpu_temp", s.cpuCritThreshold),
    post("gpu_temp", s.gpuCritThreshold),
  ]);
}

export default function Settings() {
  const { colors, theme, toggle } = useTheme();
  const { session, signOut } = useAuth();
  const [status, setStatus] = useState<StatusData | null>(null);
  const [settings, setSettings] = useState<AppSettings>(loadSettings);
  const [synced, setSynced] = useState(false);

  useEffect(() => { fetchStatus().then((d) => { if (d) setStatus(d); }); }, []);
  useEffect(() => {
    if (settings.enableNotifications && "Notification" in window && Notification.permission === "default") Notification.requestPermission();
  }, [settings.enableNotifications]);

  const updateSetting = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
    setSettings((prev) => {
      const next = { ...prev, [key]: value };
      saveSettings(next);
      if (key.includes("Threshold") || key === "enableNotifications") {
        syncThresholdsToAgent(next).then(() => { setSynced(true); setTimeout(() => setSynced(false), 2000); });
      }
      return next;
    });
  };

  return (
    <div style={{ height: "100%", overflowY: "auto", padding: "24px 28px", display: "flex", flexDirection: "column", gap: 18 }}>
      <div>
        <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 500, color: colors.accent, letterSpacing: 2, textTransform: "uppercase", marginBottom: 6 }}>Configuration</div>
        <div style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 26, fontWeight: 700, color: colors.text0 }}>Settings</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <Card label="Temperature Alerts" subLabel="Warning and critical thresholds" rightHeader={synced ? <span style={{ fontSize: 11, color: colors.accent, fontFamily: "'JetBrains Mono', monospace" }}>Synced to agent</span> : undefined}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <ThresholdRow label="CPU warning" value={settings.cpuWarnThreshold} unit="°C" onChange={(v) => updateSetting("cpuWarnThreshold", v)} min={40} max={95} colors={colors} />
            <ThresholdRow label="CPU critical" value={settings.cpuCritThreshold} unit="°C" onChange={(v) => updateSetting("cpuCritThreshold", v)} min={50} max={100} colors={colors} danger />
            <div style={{ height: 1, background: colors.border, margin: "4px 0" }} />
            <ThresholdRow label="GPU warning" value={settings.gpuWarnThreshold} unit="°C" onChange={(v) => updateSetting("gpuWarnThreshold", v)} min={40} max={95} colors={colors} />
            <ThresholdRow label="GPU critical" value={settings.gpuCritThreshold} unit="°C" onChange={(v) => updateSetting("gpuCritThreshold", v)} min={50} max={100} colors={colors} danger />
          </div>
        </Card>
        <Card label="Preferences" subLabel="Notifications and sync">
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <ToggleRow label="Temperature notifications" description="Alert when thresholds are exceeded" value={settings.enableNotifications} onChange={(v) => updateSetting("enableNotifications", v)} colors={colors} />
            <ToggleRow label="Cloud sync" description="Sync profiles and commands via Supabase" value={settings.enableCloudSync} onChange={(v) => updateSetting("enableCloudSync", v)} colors={colors} />
            <div style={{ height: 1, background: colors.border, margin: "4px 0" }} />
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 12px", background: colors.bg0, borderRadius: 8 }}>
              <span style={{ fontSize: 13, color: colors.text1 }}>Theme</span>
              <button onClick={toggle} style={{ padding: "5px 14px", borderRadius: 6, border: `0.5px solid ${colors.accentBorder}`, background: colors.accentSoft, color: colors.accent, fontSize: 12, fontWeight: 500, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace" }}>
                {theme === "light" ? "Switch to dark" : "Switch to light"}
              </button>
            </div>
          </div>
        </Card>
        <Card label="Account" subLabel="Authentication">
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <InfoPill label="Email" value={session?.user?.email || "—"} colors={colors} />
            <InfoPill label="User ID" value={session?.user?.id ? session.user.id.slice(0, 12) + "..." : "—"} colors={colors} />
            <InfoPill label="Provider" value={session?.user?.app_metadata?.provider || "email"} colors={colors} />
            <button onClick={signOut} style={{ marginTop: 8, padding: "10px 16px", borderRadius: 8, border: `0.5px solid ${colors.danger}30`, background: colors.dangerSoft, color: colors.danger, fontSize: 13, fontWeight: 500, cursor: "pointer", fontFamily: "'Inter', sans-serif" }}>Sign out</button>
          </div>
        </Card>
        <Card label="Agent" subLabel="Backend status">
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <InfoPill label="Status" value={status?.status || "Unknown"} colors={colors} accent={status?.status === "running"} />
            <InfoPill label="Device" value={status?.device_name || "—"} colors={colors} />
            <InfoPill label="Device ID" value={status?.device_id ? status.device_id.slice(0, 12) + "..." : "—"} colors={colors} />
            <InfoPill label="Mode" value={status?.demo_mode ? "Demo" : "Hardware (Legion EC)"} colors={colors} />
            <InfoPill label="Fans" value={status?.fan_count?.toString() || "0"} colors={colors} />
            <InfoPill label="Cloud" value={status?.cloud_connected ? "Connected" : "Disconnected"} colors={colors} accent={status?.cloud_connected} />
            <InfoPill label="Endpoint" value="127.0.0.1:8420" colors={colors} />
          </div>
        </Card>
      </div>
    </div>
  );
}

function ThresholdRow({ label, value, unit, onChange, min, max, colors, danger }: { label: string; value: number; unit: string; onChange: (v: number) => void; min: number; max: number; colors: any; danger?: boolean }) {
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(value.toString());
  const ac = danger ? colors.danger : colors.warn;
  const commit = () => { const n = parseInt(editValue, 10); if (!isNaN(n) && n >= min && n <= max) onChange(n); else setEditValue(value.toString()); setEditing(false); };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <span style={{ flex: 1, fontSize: 13, color: colors.text1 }}>{label}</span>
      <input type="range" min={min} max={max} value={value} onChange={(e) => { const v = Number(e.target.value); onChange(v); setEditValue(v.toString()); }} style={{ width: 120, accentColor: ac }} />
      {editing ? (
        <input type="number" value={editValue} onChange={(e) => setEditValue(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") { setEditValue(value.toString()); setEditing(false); }}} autoFocus min={min} max={max}
          style={{ width: 52, textAlign: "right", fontFamily: "'JetBrains Mono', monospace", fontSize: 12, fontWeight: 500, color: ac, background: colors.bg0, border: `1px solid ${ac}`, borderRadius: 4, padding: "2px 6px", outline: "none" }} />
      ) : (
        <span onClick={() => { setEditValue(value.toString()); setEditing(true); }} title="Click to edit"
          style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, fontWeight: 500, minWidth: 52, textAlign: "right", color: ac, cursor: "pointer", padding: "2px 6px", borderRadius: 4, border: "1px solid transparent" }}
          onMouseEnter={(e) => (e.currentTarget.style.borderColor = colors.border2)} onMouseLeave={(e) => (e.currentTarget.style.borderColor = "transparent")}>{value}{unit}</span>
      )}
    </div>
  );
}

function ToggleRow({ label, description, value, onChange, colors }: { label: string; description: string; value: boolean; onChange: (v: boolean) => void; colors: any }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 12px", background: colors.bg0, borderRadius: 8 }}>
      <div><div style={{ fontSize: 13, color: colors.text1 }}>{label}</div><div style={{ fontSize: 11, color: colors.text3, marginTop: 2 }}>{description}</div></div>
      <button onClick={() => onChange(!value)} style={{ width: 40, height: 22, borderRadius: 11, border: "none", background: value ? colors.accent : colors.bg3, cursor: "pointer", position: "relative", transition: "background 0.2s" }}>
        <span style={{ position: "absolute", top: 2, left: value ? 20 : 2, width: 18, height: 18, borderRadius: "50%", background: "#fff", transition: "left 0.2s", boxShadow: "0 1px 2px rgba(0,0,0,0.15)" }} />
      </button>
    </div>
  );
}

function InfoPill({ label, value, colors, accent }: { label: string; value: string; colors: any; accent?: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 12px", background: colors.bg0, borderRadius: 8 }}>
      <span style={{ fontSize: 12, color: colors.text2 }}>{label}</span>
      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, fontWeight: 500, color: accent ? colors.accent : colors.text0, maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{value}</span>
    </div>
  );
}