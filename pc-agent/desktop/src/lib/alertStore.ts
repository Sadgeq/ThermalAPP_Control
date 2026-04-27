// Shared alert store — Dashboard writes, Logs reads
// When backend support is added, this merges with /api/alert-log

const ALERTS_KEY = "tc-alerts";
const MAX_ALERTS = 200;

export type AlertEntry = {
  id: string;
  timestamp: string;
  severity: "info" | "warning" | "critical";
  message: string;
  source: "threshold" | "agent" | "system";
};

export function getAlerts(): AlertEntry[] {
  try {
    const raw = localStorage.getItem(ALERTS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

export function addAlert(severity: AlertEntry["severity"], message: string, source: AlertEntry["source"] = "threshold") {
  const alerts = getAlerts();
  const entry: AlertEntry = {
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    timestamp: new Date().toISOString(),
    severity,
    message,
    source,
  };
  alerts.unshift(entry); // newest first
  if (alerts.length > MAX_ALERTS) alerts.length = MAX_ALERTS;
  try { localStorage.setItem(ALERTS_KEY, JSON.stringify(alerts)); } catch {}
  return entry;
}

export function clearAlerts() {
  try { localStorage.removeItem(ALERTS_KEY); } catch {}
}