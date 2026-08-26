import { useState, useEffect } from "react";
import { useTheme } from "../lib/ThemeContext";
import { fetchAlertLog, fetchStatus, fetchHistory, fetchDiag } from "../lib/api";
import { getAlerts, clearAlerts, AlertEntry } from "../lib/alertStore";
import { supabase } from "../lib/supabase";
import Card from "../components/Card";

type CommandRow = {
  id: string;
  command_type: string;
  payload: any;
  status: "pending" | "executed" | "failed";
  created_at: string;
  executed_at: string | null;
};

export default function Logs() {
  const { colors } = useTheme();
  const [alerts, setAlerts] = useState<AlertEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [commands, setCommands] = useState<CommandRow[]>([]);

  const load = () => {
    // Merge: local threshold alerts + agent alerts
    const localAlerts = getAlerts();

    fetchAlertLog()
      .then((data) => {
        // Convert agent alerts to our format
        const agentAlerts: AlertEntry[] = (data.alerts || []).map((a: any) => ({
          id: a.id || a.timestamp,
          timestamp: a.timestamp,
          severity: a.severity || "info",
          message: a.message,
          source: "agent" as const,
        }));

        // Merge and sort by timestamp (newest first)
        const all = [...localAlerts, ...agentAlerts];
        all.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

        // Deduplicate by message+timestamp proximity (within 5s)
        const deduped: AlertEntry[] = [];
        for (const alert of all) {
          const isDupe = deduped.some(
            (d) => d.message === alert.message &&
            Math.abs(new Date(d.timestamp).getTime() - new Date(alert.timestamp).getTime()) < 5000
          );
          if (!isDupe) deduped.push(alert);
        }

        setAlerts(deduped);
        setLoading(false);
      })
      .catch(() => {
        setAlerts(localAlerts);
        setLoading(false);
      });
  };

  useEffect(() => {
    load();
    // Refresh every 5s to catch new threshold alerts from Dashboard
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  // Recent commands feed: pulls the last 50 rows from the commands table
  // for the device the agent is currently bound to. Useful for diagnosing
  // "I clicked the slider, nothing happened" — you see whether the command
  // was inserted, whether the agent picked it up (status = executed), or
  // whether something failed. Polls every 5s; the same query also re-runs
  // when the user switches to this page.
  useEffect(() => {
    let alive = true;
    const fetchCommands = async () => {
      const status = await fetchStatus();
      if (!alive || !status?.device_id) return;
      const { data, error } = await supabase
        .from("commands")
        .select("id, command_type, payload, status, created_at, executed_at")
        .eq("device_id", status.device_id)
        .order("created_at", { ascending: false })
        .limit(50);
      if (!alive) return;
      if (error) {
        console.warn("[Logs] commands fetch failed:", error.message);
        return;
      }
      setCommands((data || []) as CommandRow[]);
    };
    fetchCommands();
    const id = setInterval(fetchCommands, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const handleClear = () => {
    clearAlerts();
    setAlerts([]);
  };

  // Sensor history → CSV download. Pulls /api/history (max 180 rows by
  // default in cloud.get_sensor_history) and ships them as a Blob URL the
  // browser turns into a downloadable file. Useful for power users who
  // want to chart their thermals in Excel / NumPy.
  const [exporting, setExporting] = useState(false);
  const handleExportCsv = async () => {
    if (exporting) return;
    setExporting(true);
    try {
      const { history } = await fetchHistory();
      if (!history || history.length === 0) {
        alert("No sensor history available yet. Make sure the agent has been running and synced to the cloud.");
        return;
      }
      const headers = ["timestamp", "cpu_temp", "gpu_temp", "cpu_load", "ram_usage", "fan_avg_rpm"];
      const rows = history.map((row: any) => {
        let avgRpm = "";
        try {
          const fans = typeof row.fan_speeds === "string" ? JSON.parse(row.fan_speeds) : (row.fan_speeds || []);
          if (Array.isArray(fans) && fans.length > 0) {
            const sum = fans.reduce((a: number, f: any) => a + (Number(f.rpm) || 0), 0);
            avgRpm = String(Math.round(sum / fans.length));
          }
        } catch {}
        return [
          row.created_at ?? "",
          row.cpu_temp ?? "",
          row.gpu_temp ?? "",
          row.cpu_load ?? "",
          row.ram_usage ?? "",
          avgRpm,
        ].join(",");
      });
      const csv = [headers.join(","), ...rows].join("\n");
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `thermalcontrol-history-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      // Defer revoke so the browser has time to start the download.
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      console.warn("[Logs] CSV export failed:", err);
      alert("Export failed — see DevTools console for details.");
    } finally {
      setExporting(false);
    }
  };

  // Copy the agent's full diagnostic state to clipboard. One-click "send
  // this to support" — saves the user from running curl in a terminal.
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const handleCopyDiag = async () => {
    try {
      const diag = await fetchDiag();
      if (!diag) throw new Error("No data");
      await navigator.clipboard.writeText(JSON.stringify(diag, null, 2));
      setCopyState("copied");
      setTimeout(() => setCopyState("idle"), 2000);
    } catch (err) {
      console.warn("[Logs] copy diag failed:", err);
      setCopyState("error");
      setTimeout(() => setCopyState("idle"), 2000);
    }
  };

  const formatTime = (iso: string) => {
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch { return "—"; }
  };

  const sevColor = (sev: string) => {
    switch (sev) {
      case "warning": return { bg: colors.warnSoft, color: colors.warn };
      case "critical": case "error": return { bg: colors.dangerSoft, color: colors.danger };
      default: return { bg: colors.accentSoft, color: colors.accent };
    }
  };

  const warnings = alerts.filter((a) => a.severity === "warning").length;
  const criticals = alerts.filter((a) => a.severity === "critical" || a.severity === "error" as any).length;

  return (
    <div style={{ height: "100%", overflowY: "auto", padding: "24px 28px", display: "flex", flexDirection: "column", gap: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 500, color: colors.accent, letterSpacing: 2, textTransform: "uppercase", marginBottom: 6 }}>Event history</div>
          <div style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 26, fontWeight: 700, color: colors.text0 }}>Alert Log</div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            onClick={handleCopyDiag}
            disabled={copyState !== "idle"}
            title="Copy a JSON snapshot of the agent's full operational state to your clipboard. Paste it in a support request."
            style={{
              padding: "6px 14px", borderRadius: 6,
              border: `0.5px solid ${copyState === "copied" ? colors.accentBorder : colors.border2}`,
              background: copyState === "copied" ? colors.accentSoft : "transparent",
              color: copyState === "copied" ? colors.accent :
                     copyState === "error"  ? colors.danger : colors.text2,
              fontSize: 12, cursor: copyState === "idle" ? "pointer" : "default",
              fontFamily: "'JetBrains Mono', monospace",
              transition: "all 0.15s ease",
            }}
          >
            {copyState === "copied" ? "Copied!" : copyState === "error" ? "Failed" : "Copy diagnostics"}
          </button>
          <button
            onClick={handleExportCsv}
            disabled={exporting}
            title="Download the agent's recent sensor history as a CSV file."
            style={{
              padding: "6px 14px", borderRadius: 6,
              border: `0.5px solid ${colors.border2}`,
              background: "transparent", color: colors.text2,
              fontSize: 12, cursor: exporting ? "wait" : "pointer",
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            {exporting ? "Exporting…" : "Export CSV"}
          </button>
          {alerts.length > 0 && (
            <button onClick={handleClear} style={{
              padding: "6px 14px", borderRadius: 6,
              border: `0.5px solid ${colors.border2}`,
              background: "transparent", color: colors.text2,
              fontSize: 12, cursor: "pointer",
              fontFamily: "'JetBrains Mono', monospace",
            }}>Clear all</button>
          )}
        </div>
      </div>

      {/* Counters */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 14 }}>
        <StatCounter label="Total events" value={alerts.length} color={colors.text0} colors={colors} />
        <StatCounter label="Warnings" value={warnings} color={colors.warn} colors={colors} />
        <StatCounter label="Critical" value={criticals} color={colors.danger} colors={colors} />
      </div>

      {/* Events */}
      <Card label="Events" subLabel={`${alerts.length} total entries`}>
        {loading ? (
          <div style={{ padding: 20, textAlign: "center", color: colors.text3, fontSize: 13 }}>Loading...</div>
        ) : alerts.length === 0 ? (
          <div style={{ padding: 30, textAlign: "center", color: colors.text3, fontSize: 13 }}>
            No alerts recorded. Alerts appear here when temperature exceeds the thresholds set in Settings.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column" }}>
            {alerts.map((alert, i) => {
              const sev = sevColor(alert.severity);
              return (
                <div key={alert.id || i} style={{
                  display: "flex", alignItems: "flex-start", gap: 12,
                  padding: "12px 0",
                  borderBottom: i < alerts.length - 1 ? `0.5px solid ${colors.border}` : "none",
                }}>
                  <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: colors.text3, width: 70, flexShrink: 0, paddingTop: 2 }}>
                    {formatTime(alert.timestamp)}
                  </div>
                  <div style={{ width: 7, height: 7, borderRadius: "50%", background: sev.color, marginTop: 6, flexShrink: 0 }} />
                  <div style={{ flex: 1, fontSize: 13, color: colors.text1, lineHeight: 1.4 }}>{alert.message}</div>
                  <div style={{
                    fontFamily: "'JetBrains Mono', monospace", fontSize: 9, fontWeight: 600,
                    padding: "3px 8px", borderRadius: 4, letterSpacing: 0.5,
                    background: sev.bg, color: sev.color, flexShrink: 0, textTransform: "uppercase",
                  }}>{alert.severity}</div>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {/* Recent commands — diagnostic feed of mobile/desktop → agent traffic */}
      <Card label="Recent commands" subLabel={`${commands.length} of last 50`}>
        {commands.length === 0 ? (
          <div style={{ padding: 30, textAlign: "center", color: colors.text3, fontSize: 13 }}>
            No commands recorded yet. This list shows the last 50 fan / profile / threshold commands sent to your PC from any device.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column" }}>
            {commands.map((c, i) => {
              const statusColors =
                c.status === "executed" ? { bg: colors.accentSoft, color: colors.accent } :
                c.status === "failed"   ? { bg: colors.dangerSoft, color: colors.danger } :
                                          { bg: colors.warnSoft,   color: colors.warn };
              return (
                <div key={c.id} style={{
                  display: "flex", alignItems: "flex-start", gap: 12,
                  padding: "10px 0",
                  borderBottom: i < commands.length - 1 ? `0.5px solid ${colors.border}` : "none",
                }}>
                  <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: colors.text3, width: 70, flexShrink: 0, paddingTop: 2 }}>
                    {formatTime(c.created_at)}
                  </div>
                  <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
                    <div style={{ fontSize: 13, color: colors.text1, fontFamily: "'JetBrains Mono', monospace" }}>
                      {c.command_type}
                    </div>
                    <div style={{ fontSize: 11, color: colors.text3, fontFamily: "'JetBrains Mono', monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {c.payload ? JSON.stringify(c.payload) : ""}
                    </div>
                  </div>
                  <div style={{
                    fontFamily: "'JetBrains Mono', monospace", fontSize: 9, fontWeight: 600,
                    padding: "3px 8px", borderRadius: 4, letterSpacing: 0.5,
                    background: statusColors.bg, color: statusColors.color, flexShrink: 0, textTransform: "uppercase",
                  }}>{c.status}</div>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}

function StatCounter({ label, value, color, colors }: { label: string; value: number; color: string; colors: any }) {
  return (
    <div style={{ background: colors.card, borderRadius: 14, padding: "16px 18px", border: `0.5px solid ${colors.cardBorder}`, boxShadow: colors.shadow }}>
      <div style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 32, fontWeight: 700, color, lineHeight: 1 }}>{value}</div>
      <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 500, color: colors.text3, letterSpacing: 0.8, textTransform: "uppercase", marginTop: 6 }}>{label}</div>
    </div>
  );
}