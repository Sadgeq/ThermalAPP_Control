import { useEffect, useRef, useState, useCallback } from "react";
import { createSensorSocket, fetchSensors, SensorData } from "../lib/api";

const EMPTY: SensorData = {
  cpu_temp: null,
  cpu_load: 0,
  cpu_per_core: [],
  cpu_name: null,
  gpu_temp: null,
  gpu_hot_spot: null,
  gpu_load: null,
  gpu_name: null,
  gpu_clock_core: null,
  gpu_clock_mem: null,
  gpu_mem_used: null,
  gpu_mem_total: null,
  fan_speeds: [],
  ram_usage: 0,
  storage_temps: [],
  disk_io: { read_mb: 0, write_mb: 0 },
  network: { sent_mb: 0, recv_mb: 0 },
};

export function useSensors() {
  const [data, setData] = useState<SensorData>(EMPTY);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<number | undefined>(undefined);
  const pollRef = useRef<number | undefined>(undefined);
  const wsFailCount = useRef(0);

  // REST polling fallback
  const startPolling = useCallback(() => {
    if (pollRef.current) return;
    console.log("[Sensors] Starting REST polling fallback");
    const poll = async () => {
      const result = await fetchSensors();
      if (result) {
        setData(result);
        setConnected(true);
      }
    };
    poll();
    pollRef.current = window.setInterval(poll, 2000);
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = undefined;
    }
  }, []);

  useEffect(() => {
    function connectWs() {
      if (wsRef.current) {
        wsRef.current.close();
      }

      const ws = createSensorSocket(
        (sensorData) => {
          setData(sensorData);
          setConnected(true);
          wsFailCount.current = 0;
          stopPolling();
        },
        (isConnected) => {
          setConnected(isConnected);
          if (!isConnected) {
            wsFailCount.current++;
            if (wsFailCount.current >= 3) {
              // WebSocket keeps failing, switch to REST polling
              startPolling();
            } else {
              // Try reconnecting WebSocket
              reconnectRef.current = window.setTimeout(connectWs, 2000);
            }
          }
        }
      );

      wsRef.current = ws;
    }

    connectWs();

    // Also do an initial REST fetch so we have data immediately
    fetchSensors().then((result) => {
      if (result) {
        setData(result);
        setConnected(true);
      }
    });

    return () => {
      clearTimeout(reconnectRef.current);
      stopPolling();
      wsRef.current?.close();
    };
  }, [startPolling, stopPolling]);

  return { data, connected };
}