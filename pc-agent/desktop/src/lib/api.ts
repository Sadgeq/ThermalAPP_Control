const BASE = "http://127.0.0.1:8420";

export type SensorData = {
  cpu_temp: number | null;
  cpu_load: number;
  cpu_per_core: { name: string; load: number }[];
  cpu_name: string | null;
  gpu_temp: number | null;
  gpu_hot_spot: number | null;
  gpu_load: number | null;
  gpu_name: string | null;
  gpu_clock_core: number | null;
  gpu_clock_mem: number | null;
  gpu_mem_used: number | null;
  gpu_mem_total: number | null;
  fan_speeds: { name: string; rpm: number; percent: number | null }[];
  ram_usage: number;
  storage_temps: { drive: string; sensor: string; temp: number }[];
  disk_io: { read_mb: number; write_mb: number };
  network: { sent_mb: number; recv_mb: number };
  active_profile?: string;
};

export type StatusData = {
  status: string;
  device_id: string;
  device_name: string;
  demo_mode: boolean;
  cloud_connected: boolean;
  active_profile: string | null;
  fan_count: number;
};

export type ProfileData = {
  profiles: Record<string, {
    id: string;
    name: string;
    fan_curve: { temp: number; speed: number }[];
    is_active: boolean;
  }>;
  active: string | null;
};

async function safeFetch(url: string, options?: RequestInit) {
  try {
    const res = await fetch(url, options);
    if (!res.ok) {
      console.warn(`[API] ${url} returned ${res.status}`);
      return null;
    }
    return await res.json();
  } catch (err) {
    console.warn(`[API] ${url} failed:`, err);
    return null;
  }
}

export async function fetchStatus(): Promise<StatusData | null> {
  return safeFetch(`${BASE}/api/status`);
}

export async function fetchSensors(): Promise<SensorData | null> {
  return safeFetch(`${BASE}/api/sensors`);
}

export async function fetchProfiles(): Promise<ProfileData | null> {
  return safeFetch(`${BASE}/api/profiles`);
}

export async function activateProfile(name: string): Promise<boolean> {
  const res = await safeFetch(`${BASE}/api/profiles/${encodeURIComponent(name)}/activate`, {
    method: "POST",
  });
  return res?.ok !== false;
}
export async function setFanSpeed(index: number, percent: number): Promise<void> {
  await safeFetch(`${BASE}/api/fans/${index}/speed`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ percent }),
  });
}

export async function setAllFans(percent: number): Promise<void> {
  await safeFetch(`${BASE}/api/fans/all/speed`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ percent }),
  });
}

export type FanMode = 1 | 2 | 3;
export type FanModeState = { mode: FanMode | null; supported: boolean };

export async function fetchFanMode(): Promise<FanModeState> {
  const data = await safeFetch(`${BASE}/api/fan-mode`);
  return data || { mode: null, supported: false };
}

export async function setFanMode(mode: FanMode): Promise<boolean> {
  const res = await safeFetch(`${BASE}/api/fan-mode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  return res?.ok === true;
}

export async function fetchAlertLog(): Promise<{ alerts: any[] }> {
  const data = await safeFetch(`${BASE}/api/alert-log`);
  return data || { alerts: [] };
}

export async function fetchHistory(): Promise<{ history: any[] }> {
  const data = await safeFetch(`${BASE}/api/history`);
  return data || { history: [] };
}

export function createSensorSocket(
  onData: (data: SensorData) => void,
  onStatus?: (connected: boolean) => void
): WebSocket {
  console.log("[WS] Connecting to ws://127.0.0.1:8420/ws/sensors");
  const ws = new WebSocket("ws://127.0.0.1:8420/ws/sensors");

  ws.onopen = () => {
    console.log("[WS] Connected");
    onStatus?.(true);
  };
  ws.onclose = (e) => {
    console.log("[WS] Closed:", e.code, e.reason);
    onStatus?.(false);
  };
  ws.onerror = (e) => {
    console.log("[WS] Error:", e);
    onStatus?.(false);
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type !== "alert") {
        onData(data);
      }
    } catch {}
  };

  return ws;
}