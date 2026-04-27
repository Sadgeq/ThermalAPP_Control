import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";

export type SensorData = {
  cpu_temp: number | null;
  gpu_temp: number | null;
  cpu_load: number;
  gpu_load: number;
  ram_usage: number;
  fan_speeds: { name?: string; rpm: number; percent: number | null }[];
  cpu_per_core: { core: number; load: number }[];
  gpu_clock_core: number | null;
  gpu_clock_mem: number | null;
  active_profile: string | null;
  cpu_name: string | null;
  gpu_name: string | null;
  timestamp: string;
};

const EMPTY: SensorData = {
  cpu_temp: null,
  gpu_temp: null,
  cpu_load: 0,
  gpu_load: 0,
  ram_usage: 0,
  fan_speeds: [],
  cpu_per_core: [],
  gpu_clock_core: null,
  gpu_clock_mem: null,
  active_profile: null,
  cpu_name: null,
  gpu_name: null,
  timestamp: "",
};

export function useRealtimeSensors(deviceId: string | null) {
  const [data, setData] = useState<SensorData>(EMPTY);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!deviceId) return;

    // Initial fetch: get the latest reading
    const fetchLatest = async () => {
      const { data: rows, error } = await supabase
        .from("sensor_readings")
        .select("*")
        .eq("device_id", deviceId)
        .order("created_at", { ascending: false })
        .limit(1);

      if (error) {
        console.log("Sensor fetch error:", error.message);
        return;
      }
      if (rows && rows.length > 0) {
        const parsed = parseReading(rows[0]);
        setData(parsed);
        setConnected(true);
      }
    };

    fetchLatest();

    // Also fetch device info for active profile
    const fetchDevice = async () => {
      const { data: device } = await supabase
        .from("devices")
        .select("*")
        .eq("id", deviceId)
        .single();
      if (device) {
        setData((prev) => ({
          ...prev,
          cpu_name: device.cpu_name || prev.cpu_name,
          gpu_name: device.gpu_name || prev.gpu_name,
        }));
      }
    };
    fetchDevice();

    // Fetch active profile
    const fetchProfile = async () => {
      const { data: profiles } = await supabase
        .from("profiles")
        .select("name, is_active")
        .eq("device_id", deviceId)
        .eq("is_active", true)
        .limit(1);
      if (profiles && profiles.length > 0) {
        setData((prev) => ({
          ...prev,
          active_profile: profiles[0].name,
        }));
      }
    };
    fetchProfile();

    // Subscribe to new sensor readings in real-time
    const channel = supabase
      .channel(`sensors:${deviceId}`)
      .on(
        "postgres_changes",
        {
          event: "INSERT",
          schema: "public",
          table: "sensor_readings",
          filter: `device_id=eq.${deviceId}`,
        },
        (payload) => {
          const parsed = parseReading(payload.new);
          setData((prev) => ({
            ...parsed,
            // Keep device info from previous state (not in sensor_readings table)
            cpu_name: prev.cpu_name,
            gpu_name: prev.gpu_name,
            active_profile: prev.active_profile,
          }));
          setConnected(true);
        }
      )
      .subscribe();

    // Also subscribe to profile changes
    const profileChannel = supabase
      .channel(`profiles:${deviceId}`)
      .on(
        "postgres_changes",
        {
          event: "UPDATE",
          schema: "public",
          table: "profiles",
          filter: `device_id=eq.${deviceId}`,
        },
        () => {
          // Re-fetch active profile on any profile update
          fetchProfile();
        }
      )
      .subscribe();

    // Poll for fresh data every 5s as fallback (in case Realtime isn't enabled)
    const pollInterval = setInterval(fetchLatest, 5000);

    return () => {
      supabase.removeChannel(channel);
      supabase.removeChannel(profileChannel);
      clearInterval(pollInterval);
    };
  }, [deviceId]);

  return { data, connected };
}

function parseFans(raw: any): { name?: string; rpm: number; percent: number | null }[] {
  // pc-agent inserts fan_speeds as a JSON string via json.dumps(). Supabase's
  // jsonb column may return it as a parsed array OR as a string depending on
  // table schema. Handle both.
  if (Array.isArray(raw)) return raw;
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }
  return [];
}

function parseReading(row: any): SensorData {
  return {
    cpu_temp: row.cpu_temp ?? null,
    gpu_temp: row.gpu_temp ?? null,
    cpu_load: row.cpu_load ?? 0,
    gpu_load: row.gpu_load ?? 0,
    ram_usage: row.ram_usage ?? 0,
    fan_speeds: parseFans(row.fan_speeds),
    cpu_per_core: Array.isArray(row.cpu_per_core) ? row.cpu_per_core : [],
    gpu_clock_core: row.gpu_clock_core ?? null,
    gpu_clock_mem: row.gpu_clock_mem ?? null,
    active_profile: row.active_profile ?? null,
    cpu_name: row.cpu_name ?? null,
    gpu_name: row.gpu_name ?? null,
    timestamp: row.created_at || new Date().toISOString(),
  };
}