import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useAuth } from "@/lib/auth-context";

export type Device = {
  id: string;
  name: string;
  hardware_id: string;
  is_online: boolean;
  last_seen: string | null;
  os_info: string | null;
  // Fan-control driver advertised by the agent on heartbeat. Stable
  // identifier ('lenovo-legion-wmi', 'lhm-pwm', 'sensors-only', 'demo').
  // Empty string for legacy agents that pre-date the controller column.
  controller: string;
  // Agent semantic version, stamped on heartbeat. Empty for legacy agents.
  app_version: string;
};

// Match the server-side public.device_is_online() function: a device is
// online if its last heartbeat is within the past 90 seconds. Computing
// this client-side rather than trusting the devices.is_online column
// means a crashed agent (which can't update the column to false) doesn't
// keep showing as green.
const ONLINE_THRESHOLD_MS = 90 * 1000;

function isFreshHeartbeat(lastSeen: string | null): boolean {
  if (!lastSeen) return false;
  const t = Date.parse(lastSeen);
  if (Number.isNaN(t)) return false;
  return Date.now() - t < ONLINE_THRESHOLD_MS;
}

export function useDevices() {
  const { user } = useAuth();
  const [devices, setDevices] = useState<Device[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!user) {
      setDevices([]);
      setSelectedId(null);
      setLoading(false);
      return;
    }

    const fetch = async () => {
      const { data } = await supabase
        .from("devices")
        .select("*")
        .eq("user_id", user.id)
        .order("last_seen", { ascending: false });

      const list: Device[] = (data || []).map((d: any) => ({
        id: d.id,
        name: d.name || "PC",
        hardware_id: d.hardware_id,
        is_online: isFreshHeartbeat(d.last_seen),
        last_seen: d.last_seen,
        os_info: d.os_info,
        controller: d.controller || "",
        app_version: d.app_version || "",
      }));

      setDevices(list);
      if (list.length > 0 && !selectedId) {
        const online = list.find((d) => d.is_online);
        setSelectedId(online?.id ?? list[0].id);
      }
      setLoading(false);
    };
    fetch();

    // Re-evaluate freshness every 15s without re-fetching. Heartbeats
    // arrive every 30s; this gives mobile a snappy "went offline" badge
    // even when no other state is changing.
    const tick = setInterval(() => {
      setDevices((prev) =>
        prev.map((d) => ({ ...d, is_online: isFreshHeartbeat(d.last_seen) }))
      );
    }, 15_000);
    return () => clearInterval(tick);
  }, [user]);

  return { devices, selectedId, setSelectedId, loading };
}
