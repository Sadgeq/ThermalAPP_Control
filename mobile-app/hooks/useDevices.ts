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
};

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
        is_online: d.is_online ?? false,
        last_seen: d.last_seen,
        os_info: d.os_info,
      }));

      setDevices(list);
      // Auto-select first online device, or first device
      if (list.length > 0 && !selectedId) {
        const online = list.find((d) => d.is_online);
        setSelectedId(online?.id ?? list[0].id);
      }
      setLoading(false);
    };
    fetch();
  }, [user]);

  return { devices, selectedId, setSelectedId, loading };
}
