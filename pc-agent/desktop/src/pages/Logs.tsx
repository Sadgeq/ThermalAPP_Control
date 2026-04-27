import { useState, useEffect } from "react";
import { useTheme } from "../lib/ThemeContext";
import { fetchAlertLog } from "../lib/api";
import { getAlerts, clearAlerts, AlertEntry } from "../lib/alertStore";
import Card from "../components/Card";

export default function Logs() {
  const { colors } = useTheme();
  const [alerts, setAlerts] = useState<AlertEntry[]>([]);
  const [loading, setLoading] = useState(true);

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

  const handleClear = () => {
    clearAlerts();
    setAlerts([]);
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
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 500, color: colors.accent, letterSpacing: 2, textTransform: "uppercase", marginBottom: 6 }}>Event history</div>
          <div style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 26, fontWeight: 700, color: colors.text0 }}>Alert Log</div>
        </div>
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