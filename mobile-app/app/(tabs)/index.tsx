import React, { useEffect, useMemo, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  useWindowDimensions,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import Svg, { Path, Line, Rect, Defs, LinearGradient, Stop } from "react-native-svg";
import { colors, radius, spacing, type, tempColor, loadColor } from "@/lib/theme";
import { useDevices } from "@/hooks/useDevices";
import { useRealtimeSensors } from "@/hooks/useRealtimeSensors";
import { supabase } from "@/lib/supabase";

type HistoryPoint = { t: number; cpu: number | null; gpu: number | null };

export default function DashboardScreen() {
  const { width } = useWindowDimensions();
  const contentMaxW = Math.min(width, 560);
  const innerW = contentMaxW - spacing.lg * 2;

  const { devices, selectedId, loading: devLoading } = useDevices();
  const { data, connected } = useRealtimeSensors(selectedId);
  const device = devices.find((d) => d.id === selectedId);

  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [alertsToday, setAlertsToday] = useState<number>(0);

  // Temperature history — last 60 readings (~2 minutes at 2s polling)
  useEffect(() => {
    if (!selectedId) return;
    let alive = true;
    const fetchHistory = async () => {
      const { data: rows } = await supabase
        .from("sensor_readings")
        .select("created_at, cpu_temp, gpu_temp")
        .eq("device_id", selectedId)
        .order("created_at", { ascending: false })
        .limit(60);
      if (!alive || !rows) return;
      setHistory(
        rows.reverse().map((r: any) => ({
          t: new Date(r.created_at).getTime(),
          cpu: r.cpu_temp,
          gpu: r.gpu_temp,
        }))
      );
    };
    fetchHistory();
    const id = setInterval(fetchHistory, 8_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [selectedId]);

  // 24h alert count
  useEffect(() => {
    if (!selectedId) return;
    const since = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
    supabase
      .from("alerts")
      .select("id", { count: "exact", head: true })
      .eq("device_id", selectedId)
      .gte("created_at", since)
      .then((res) => setAlertsToday(res.count ?? 0));
  }, [selectedId, data.timestamp]);

  // Compute min/max/avg from history
  const stats = useMemo(() => {
    const cpu = history.map((p) => p.cpu).filter((v): v is number => v != null);
    const gpu = history.map((p) => p.gpu).filter((v): v is number => v != null);
    return {
      cpuMin: cpu.length ? Math.min(...cpu) : null,
      cpuMax: cpu.length ? Math.max(...cpu) : null,
      cpuAvg: cpu.length ? cpu.reduce((a, b) => a + b, 0) / cpu.length : null,
      gpuMin: gpu.length ? Math.min(...gpu) : null,
      gpuMax: gpu.length ? Math.max(...gpu) : null,
      gpuAvg: gpu.length ? gpu.reduce((a, b) => a + b, 0) / gpu.length : null,
    };
  }, [history]);

  const avgFan =
    data.fan_speeds.length > 0
      ? data.fan_speeds.reduce((a, f) => a + (f.percent || 0), 0) /
        data.fan_speeds.length
      : 0;

  // "Last Sync" formatted as HH:MM
  const lastSyncLabel = useMemo(() => {
    if (!device?.last_seen) return "—";
    const d = new Date(device.last_seen);
    const hh = d.getHours().toString().padStart(2, "0");
    const mm = d.getMinutes().toString().padStart(2, "0");
    return `${hh}:${mm}`;
  }, [device?.last_seen]);

  // Truncated device id for the subtitle pill
  const deviceIdShort = useMemo(() => {
    if (!device?.id) return "—";
    return `device_${device.id.slice(0, 8)}`;
  }, [device?.id]);

  // Machine column: distill os_info into a tight label.
  const machineShort = useMemo(() => {
    const os = device?.os_info ?? "";
    if (!os) return "—";
    // "Windows 10 (10.0.19041)" → "Windows 10"
    const cleaned = os.split("(")[0].trim();
    return cleaned.length > 14 ? cleaned.slice(0, 14) + "…" : cleaned;
  }, [device?.os_info]);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <ScrollView
        contentContainerStyle={[
          styles.content,
          { alignSelf: "center", maxWidth: contentMaxW, width: "100%" },
        ]}
        showsVerticalScrollIndicator={false}
      >
        {/* === DEVICE CARD === */}
        <View style={styles.deviceCard}>
          {/* Top row: icon + identity + status */}
          <View style={styles.deviceTop}>
            <View style={styles.deviceIcon}>
              <Svg width={22} height={22} viewBox="0 0 24 24" fill="none"
                stroke={colors.accent} strokeWidth={2}
                strokeLinecap="round" strokeLinejoin="round">
                <Rect x="2" y="3" width="20" height="6" rx="2" />
                <Rect x="2" y="13" width="20" height="6" rx="2" />
                <Path d="M6 6h.01M6 16h.01" />
              </Svg>
            </View>
            <View style={styles.deviceIdentity}>
              <Text style={styles.deviceTitle} numberOfLines={1}>
                {device?.name ?? "PC Agent"}
              </Text>
              <Text style={styles.deviceSubId} numberOfLines={1}>
                {deviceIdShort}
              </Text>
            </View>
            <View
              style={[
                styles.onlinePill,
                {
                  backgroundColor: connected ? colors.accentSoft : colors.dangerSoft,
                  borderColor: connected ? "rgba(61,220,151,0.3)" : "rgba(250,82,82,0.3)",
                },
              ]}
            >
              <View
                style={[
                  styles.onlineDot,
                  { backgroundColor: connected ? colors.accent : colors.danger },
                ]}
              />
              <Text
                style={[
                  styles.onlinePillText,
                  { color: connected ? colors.accent : colors.danger },
                ]}
              >
                {connected ? "Online" : devLoading ? "Loading" : "Offline"}
              </Text>
            </View>
          </View>

          {/* 3-col stat strip */}
          <View style={styles.deviceStrip}>
            <DeviceStat
              label="Machine"
              value={machineShort}
              icon={
                <Svg width={12} height={12} viewBox="0 0 24 24" fill="none"
                  stroke={colors.text2} strokeWidth={2} strokeLinecap="round"
                  strokeLinejoin="round">
                  <Rect x="2" y="3" width="20" height="14" rx="2" />
                  <Line x1="8" y1="21" x2="16" y2="21" />
                  <Line x1="12" y1="17" x2="12" y2="21" />
                </Svg>
              }
            />
            <View style={styles.deviceStripDivider} />
            <DeviceStat
              label="Sync"
              value={connected ? "Live" : "—"}
              valueColor={connected ? colors.accent : colors.text3}
              icon={
                <Svg width={12} height={12} viewBox="0 0 24 24" fill="none"
                  stroke={colors.text2} strokeWidth={2} strokeLinecap="round"
                  strokeLinejoin="round">
                  <Path d="M21 12a9 9 0 11-9-9 9 9 0 019 9z" />
                  <Path d="M12 7v5l3 3" />
                </Svg>
              }
            />
            <View style={styles.deviceStripDivider} />
            <DeviceStat
              label="Last Sync"
              value={lastSyncLabel}
              icon={
                <Svg width={12} height={12} viewBox="0 0 24 24" fill="none"
                  stroke={colors.text2} strokeWidth={2} strokeLinecap="round"
                  strokeLinejoin="round">
                  <Path d="M3 12a9 9 0 0118 0M12 21a9 9 0 01-9-9" />
                  <Path d="M21 12L18 9M21 12l-3 3" />
                </Svg>
              }
            />
          </View>
        </View>

        {/* === DUAL TEMP HERO === */}
        <View style={styles.tempRow}>
          <TempHero
            label="CPU"
            value={data.cpu_temp}
            min={stats.cpuMin}
            max={stats.cpuMax}
            avg={stats.cpuAvg}
          />
          <TempHero
            label="GPU"
            value={data.gpu_temp}
            min={stats.gpuMin}
            max={stats.gpuMax}
            avg={stats.gpuAvg}
          />
        </View>

        {/* === Active profile + alerts chips === */}
        <View style={styles.chipRow}>
          <View style={styles.profileChip}>
            <Text style={[type.eyebrow, { fontSize: 9, color: colors.text3 }]}>
              Active profile
            </Text>
            <Text style={styles.chipValue}>
              {data.active_profile ?? "—"}
            </Text>
          </View>
          <View
            style={[
              styles.alertsChip,
              alertsToday > 0 && {
                backgroundColor: colors.warnSoft,
                borderColor: "rgba(245,165,36,0.35)",
              },
            ]}
          >
            <Text style={[type.eyebrow, { fontSize: 9, color: colors.text3 }]}>
              Alerts · 24h
            </Text>
            <Text
              style={[
                styles.chipValue,
                { color: alertsToday > 0 ? colors.warn : colors.text1 },
              ]}
            >
              {alertsToday}
            </Text>
          </View>
        </View>

        {/* === HISTORY CHART === */}
        {history.length > 2 && (
          <View style={[styles.section, { marginTop: spacing.xxl }]}>
            <View style={styles.chartCard}>
              <View style={styles.chartHeader}>
                <View>
                  <Text style={type.eyebrow}>Last 2 minutes</Text>
                  <Text style={styles.chartTitle}>Temperature</Text>
                </View>
                <View style={styles.chartLegend}>
                  <ChartLegendChip
                    color={colors.accent}
                    label="CPU"
                    value={data.cpu_temp != null ? Math.round(data.cpu_temp) : null}
                  />
                  <ChartLegendChip
                    color={colors.warn}
                    label="GPU"
                    value={data.gpu_temp != null ? Math.round(data.gpu_temp) : null}
                  />
                </View>
              </View>
              <TempChart
                history={history}
                maxWidth={innerW - spacing.lg * 2}
              />
            </View>
          </View>
        )}

        {/* === PERFORMANCE === */}
        <View style={styles.section}>
          <Text style={[type.eyebrow, styles.sectionTitle]}>Performance</Text>
          <View style={styles.statGroup}>
            <PerfRow
              label="CPU Load"
              value={`${Math.round(data.cpu_load)}%`}
              percent={data.cpu_load}
              color={loadColor(data.cpu_load)}
              icon={<CpuIcon color={colors.text2} />}
            />
            <PerfRow
              label="Memory"
              value={`${Math.round(data.ram_usage)}%`}
              percent={data.ram_usage}
              color={loadColor(data.ram_usage)}
              icon={<MemoryIcon color={colors.text2} />}
            />
            <PerfRow
              label="Avg Fan"
              value={`${Math.round(avgFan)}%`}
              percent={avgFan}
              color={colors.cyan}
              icon={<FanIcon color={colors.text2} />}
              last
            />
          </View>
        </View>

        {/* === FANS === */}
        {data.fan_speeds.length > 0 && (
          <View style={styles.section}>
            <Text style={[type.eyebrow, styles.sectionTitle]}>Fans</Text>
            <View style={styles.fanGrid}>
              {data.fan_speeds.map((f: any, i) => (
                <FanCard
                  key={i}
                  name={f.name || `Fan ${i + 1}`}
                  rpm={f.rpm ?? 0}
                  percent={f.percent ?? 0}
                />
              ))}
            </View>
          </View>
        )}

        <View style={{ height: spacing.xxxxl }} />
      </ScrollView>
    </SafeAreaView>
  );
}

/* --- Sub-components --------------------------------------------------- */

function DeviceStat({
  label,
  value,
  icon,
  valueColor,
}: {
  label: string;
  value: string;
  icon?: React.ReactNode;
  valueColor?: string;
}) {
  return (
    <View style={styles.deviceStat}>
      <View style={styles.deviceStatLabel}>
        {icon}
        <Text style={styles.deviceStatLabelText}>{label}</Text>
      </View>
      <Text
        style={[styles.deviceStatValue, valueColor ? { color: valueColor } : null]}
        numberOfLines={1}
      >
        {value}
      </Text>
    </View>
  );
}

function TempHero({
  label,
  value,
  min,
  max,
  avg,
}: {
  label: string;
  value: number | null;
  min: number | null;
  max: number | null;
  avg: number | null;
}) {
  const color = tempColor(value);
  const has = value != null;
  const fillPct = has ? Math.min(100, Math.max(2, (value! / 100) * 100)) : 0;
  return (
    <View style={styles.tempCard}>
      <View style={styles.tempLabelRow}>
        <Text style={[type.eyebrow, { fontSize: 10 }]}>{label}</Text>
        <View style={[styles.tempStatusDot, { backgroundColor: color }]} />
      </View>
      <View style={styles.tempValueRow}>
        <Text style={[styles.tempValue, { color: has ? colors.text0 : colors.text3 }]}>
          {has ? Math.round(value!) : "—"}
        </Text>
        {has && <Text style={styles.tempUnit}>°C</Text>}
      </View>
      <View style={styles.tempBar}>
        <View style={[styles.tempBarFill, { width: `${fillPct}%`, backgroundColor: color }]} />
      </View>
      <View style={styles.tempStats}>
        <TempStat label="MIN" value={min} />
        <TempStat label="AVG" value={avg} />
        <TempStat label="MAX" value={max} />
      </View>
    </View>
  );
}

function TempStat({ label, value }: { label: string; value: number | null }) {
  return (
    <View style={styles.tempStat}>
      <Text style={[type.eyebrow, { fontSize: 8, color: colors.text3, letterSpacing: 1 }]}>
        {label}
      </Text>
      <Text style={styles.tempStatValue}>
        {value != null ? `${Math.round(value)}°` : "—"}
      </Text>
    </View>
  );
}

function PerfRow({
  label,
  value,
  percent,
  color,
  icon,
  last,
}: {
  label: string;
  value: string;
  percent: number;
  color: string;
  icon?: React.ReactNode;
  last?: boolean;
}) {
  const pct = Math.max(0, Math.min(100, percent));
  return (
    <View style={[styles.perfRow, !last && styles.perfRowBorder]}>
      <View style={styles.perfRowTop}>
        <View style={styles.perfRowLeft}>
          <View style={styles.perfIconChip}>{icon}</View>
          <Text style={styles.perfLabel}>{label}</Text>
        </View>
        <Text style={[styles.perfValue, { color }]}>{value}</Text>
      </View>
      <View style={styles.perfBarTrack}>
        <View style={[styles.perfBarFill, { width: `${pct}%`, backgroundColor: color }]} />
      </View>
    </View>
  );
}

function FanCard({ name, rpm, percent }: { name: string; rpm: number; percent: number }) {
  const pct = Math.max(0, Math.min(100, percent));
  return (
    <View style={styles.fanCard}>
      <View style={styles.fanCardTop}>
        <View style={styles.fanCardIcon}>
          <FanIcon color={colors.accent} />
        </View>
        <Text style={[type.eyebrow, { fontSize: 9 }]}>{name.toUpperCase()}</Text>
      </View>
      <View style={styles.fanCardValueRow}>
        <Text style={styles.fanCardRpm}>{rpm.toLocaleString()}</Text>
        <Text style={styles.fanCardUnit}>RPM</Text>
      </View>
      <View style={styles.fanCardBarTrack}>
        <View style={[styles.fanCardBarFill, { width: `${pct}%` }]} />
      </View>
      <Text style={styles.fanCardPct}>{Math.round(pct)}% of max</Text>
    </View>
  );
}

/* Icons */
function CpuIcon({ color }: { color: string }) {
  return (
    <Svg width={16} height={16} viewBox="0 0 24 24" fill="none"
      stroke={color} strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round">
      <Rect x="4" y="4" width="16" height="16" rx="2" />
      <Rect x="9" y="9" width="6" height="6" />
      <Path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3" />
    </Svg>
  );
}
function MemoryIcon({ color }: { color: string }) {
  return (
    <Svg width={16} height={16} viewBox="0 0 24 24" fill="none"
      stroke={color} strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round">
      <Rect x="3" y="7" width="18" height="12" rx="1" />
      <Path d="M7 11v4M11 11v4M15 11v4M19 11v4M5 21h2M9 21h2M13 21h2M17 21h2" />
    </Svg>
  );
}
function FanIcon({ color }: { color: string }) {
  return (
    <Svg width={16} height={16} viewBox="0 0 24 24" fill="none"
      stroke={color} strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round">
      <Path d="M12 12c-3-2.5-3-7 0-7s3 4.5 0 7zM12 12c2.5 3 7 3 7 0s-4.5-3-7 0zM12 12c3 2.5 3 7 0 7s-3-4.5 0-7zM12 12c-2.5-3-7-3-7 0s4.5 3 7 0z" />
      <Path d="M12 12.01" />
    </Svg>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <View style={styles.legendItem}>
      <View style={[styles.legendDot, { backgroundColor: color }]} />
      <Text style={[type.caption, { fontSize: 10, color: colors.text2 }]}>{label}</Text>
    </View>
  );
}

function TempChart({ history, maxWidth }: { history: HistoryPoint[]; maxWidth: number }) {
  // The card hosting this chart has its own padding; `maxWidth` is the full
  // available width inside the card. Reserve `yAxisW` on the left for the
  // (future) labels / tick offset, the rest is the line plot area.
  const yAxisW = 14;
  const totalW = Math.max(160, maxWidth);
  const chartW = totalW - yAxisW;
  const height = 160;
  const padX = 6;
  const padY = 14;

  const allTemps = history.flatMap((h) => [h.cpu, h.gpu]).filter((x): x is number => x != null);
  if (allTemps.length < 2) return null;

  // Snap min/max to nearest 10 so axis ticks are clean numbers
  const rawMin = Math.min(...allTemps);
  const rawMax = Math.max(...allTemps);
  const minY = Math.max(0, Math.floor((rawMin - 5) / 10) * 10);
  const maxY = Math.min(110, Math.ceil((rawMax + 5) / 10) * 10);

  const toX = (i: number) =>
    yAxisW + padX + (i / Math.max(1, history.length - 1)) * (chartW - padX * 2);
  const toY = (v: number) =>
    padY + (1 - (v - minY) / Math.max(1, maxY - minY)) * (height - padY * 2);

  const buildPath = (key: "cpu" | "gpu") => {
    let d = "";
    history.forEach((p, i) => {
      const v = p[key];
      if (v == null) return;
      d += (d ? " L" : "M") + ` ${toX(i).toFixed(1)} ${toY(v).toFixed(1)}`;
    });
    return d;
  };

  const cpuPath = buildPath("cpu");
  const gpuPath = buildPath("gpu");
  const cpuArea = cpuPath
    ? `${cpuPath} L ${toX(history.length - 1).toFixed(1)} ${height - padY} L ${toX(0).toFixed(1)} ${height - padY} Z`
    : "";

  // 4 evenly spaced ticks
  const ticks = [0, 0.33, 0.66, 1].map((f) => ({
    y: padY + (1 - f) * (height - padY * 2),
    value: Math.round(minY + f * (maxY - minY)),
  }));

  // Last data points for end-of-line dots
  const lastIdx = history.length - 1;
  const lastCpu = history[lastIdx].cpu;
  const lastGpu = history[lastIdx].gpu;

  return (
    <Svg width={totalW} height={height}>
      <Defs>
        <LinearGradient id="cpuGrad" x1="0" y1="0" x2="0" y2="1">
          <Stop offset="0%" stopColor={colors.accent} stopOpacity="0.28" />
          <Stop offset="100%" stopColor={colors.accent} stopOpacity="0" />
        </LinearGradient>
      </Defs>

      {/* Y-axis tick lines + labels */}
      {ticks.map((t, i) => (
        <React.Fragment key={i}>
          <Line
            x1={yAxisW}
            x2={totalW - padX}
            y1={t.y}
            y2={t.y}
            stroke={colors.hairline}
            strokeWidth="0.5"
            strokeDasharray={i === 0 || i === ticks.length - 1 ? undefined : "2,3"}
          />
          {/* Render the label as an SVG text via a workaround — react-native-svg
              has Text but to keep imports minimal, use Path for tick lines only
              and overlay labels via React Native Text positioned absolutely.
              We do this in the parent. */}
        </React.Fragment>
      ))}

      {cpuArea ? <Path d={cpuArea} fill="url(#cpuGrad)" /> : null}
      {gpuPath ? (
        <Path d={gpuPath} fill="none" stroke={colors.warn} strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" />
      ) : null}
      {cpuPath ? (
        <Path d={cpuPath} fill="none" stroke={colors.accent} strokeWidth="2.4"
          strokeLinecap="round" strokeLinejoin="round" />
      ) : null}

      {/* End-of-line dots */}
      {lastCpu != null && (
        <>
          <Path
            d={`M ${toX(lastIdx)} ${toY(lastCpu)} m -4 0 a 4 4 0 1 0 8 0 a 4 4 0 1 0 -8 0`}
            fill={colors.accent}
            stroke={colors.bg1}
            strokeWidth="2"
          />
        </>
      )}
      {lastGpu != null && (
        <>
          <Path
            d={`M ${toX(lastIdx)} ${toY(lastGpu)} m -3.5 0 a 3.5 3.5 0 1 0 7 0 a 3.5 3.5 0 1 0 -7 0`}
            fill={colors.warn}
            stroke={colors.bg1}
            strokeWidth="2"
          />
        </>
      )}
    </Svg>
  );
}

function ChartLegendChip({
  color,
  label,
  value,
}: {
  color: string;
  label: string;
  value: number | null;
}) {
  return (
    <View style={styles.chartLegendChip}>
      <View style={[styles.legendDot, { backgroundColor: color }]} />
      <Text style={styles.chartLegendLabel}>{label}</Text>
      <Text style={[styles.chartLegendValue, { color }]}>
        {value != null ? `${value}°` : "—"}
      </Text>
    </View>
  );
}

/* --- Styles ---------------------------------------------------------- */

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg0 },
  content: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm },

  // === DEVICE CARD ===
  deviceCard: {
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: colors.border,
    padding: spacing.lg,
    marginTop: spacing.md,
    gap: spacing.lg,
  },
  deviceTop: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
  },
  deviceIcon: {
    width: 44,
    height: 44,
    borderRadius: radius.md,
    backgroundColor: colors.accentSoft,
    borderWidth: 0.5,
    borderColor: "rgba(61,220,151,0.25)",
    alignItems: "center",
    justifyContent: "center",
  },
  deviceIdentity: { flex: 1, gap: 2 },
  deviceTitle: {
    ...type.titleM,
    fontSize: 18,
    fontWeight: "700",
    letterSpacing: -0.3,
  },
  deviceSubId: {
    ...type.mono,
    fontSize: 12,
    color: colors.text3,
  },
  onlinePill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: radius.pill,
    borderWidth: 0.5,
  },
  onlineDot: { width: 6, height: 6, borderRadius: 3 },
  onlinePillText: {
    ...type.caption,
    fontSize: 11,
    fontWeight: "700",
  },
  deviceStrip: {
    flexDirection: "row",
    alignItems: "stretch",
    backgroundColor: colors.bg2,
    borderRadius: radius.md,
    borderWidth: 0.5,
    borderColor: colors.hairline,
    overflow: "hidden",
  },
  deviceStripDivider: { width: 0.5, backgroundColor: colors.separator },
  deviceStat: {
    flex: 1,
    paddingVertical: 12,
    paddingHorizontal: 12,
    gap: 4,
  },
  deviceStatLabel: { flexDirection: "row", alignItems: "center", gap: 5 },
  deviceStatLabelText: {
    ...type.caption,
    fontSize: 11,
    color: colors.text2,
    fontWeight: "500",
  },
  deviceStatValue: {
    ...type.bodyStrong,
    color: colors.text0,
    fontSize: 15,
    fontWeight: "700",
    letterSpacing: -0.2,
  },

  // === DUAL TEMP HERO ===
  tempRow: {
    flexDirection: "row",
    gap: spacing.md,
    marginTop: spacing.lg,
  },
  tempCard: {
    flex: 1,
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: colors.border,
    padding: spacing.lg,
    gap: spacing.md,
  },
  tempLabelRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  tempStatusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  tempValueRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    marginTop: -6,
  },
  tempValue: {
    fontSize: 56,
    fontWeight: "800",
    letterSpacing: -2.4,
    lineHeight: 56,
    color: colors.text0,
    fontVariant: ["tabular-nums"],
  },
  tempUnit: {
    ...type.titleS,
    color: colors.text2,
    fontSize: 16,
    fontWeight: "600",
    paddingTop: 8,
    paddingLeft: 2,
  },
  tempBar: {
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.bg3,
    overflow: "hidden",
  },
  tempBarFill: { height: "100%", borderRadius: 2 },
  tempStats: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 2,
  },
  tempStat: { gap: 2 },
  tempStatValue: {
    ...type.mono,
    fontSize: 13,
    color: colors.text1,
    fontWeight: "600",
    fontVariant: ["tabular-nums"],
  },

  // === Chips row ===
  chipRow: {
    flexDirection: "row",
    gap: spacing.md,
    marginTop: spacing.md,
  },
  profileChip: {
    flex: 1.2,
    backgroundColor: colors.bg1,
    borderRadius: radius.md,
    borderWidth: 0.5,
    borderColor: colors.border,
    paddingHorizontal: spacing.md,
    paddingVertical: 10,
    gap: 2,
  },
  alertsChip: {
    flex: 1,
    backgroundColor: colors.bg1,
    borderRadius: radius.md,
    borderWidth: 0.5,
    borderColor: colors.border,
    paddingHorizontal: spacing.md,
    paddingVertical: 10,
    gap: 2,
  },
  chipValue: {
    ...type.titleS,
    fontSize: 18,
    fontWeight: "700",
    color: colors.accent,
    fontVariant: ["tabular-nums"],
  },

  // === Sections ===
  section: { marginTop: spacing.xxl },
  sectionTitle: { marginBottom: spacing.sm, paddingHorizontal: spacing.xs },
  sectionHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: spacing.sm,
    paddingHorizontal: spacing.xs,
  },
  legend: { flexDirection: "row", gap: spacing.md },
  legendItem: { flexDirection: "row", alignItems: "center", gap: 5 },
  legendDot: { width: 6, height: 6, borderRadius: 3 },

  // Chart card
  chartCard: {
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: colors.border,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.lg,
    paddingBottom: spacing.md,
    gap: spacing.md,
  },
  chartHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
  },
  chartTitle: {
    ...type.titleS,
    fontSize: 18,
    fontWeight: "700",
    letterSpacing: -0.3,
    marginTop: 2,
  },
  chartLegend: {
    gap: 6,
    alignItems: "flex-end",
  },
  chartLegendChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.bg2,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: radius.sm,
    borderWidth: 0.5,
    borderColor: colors.border,
  },
  chartLegendLabel: {
    ...type.eyebrow,
    fontSize: 9,
    color: colors.text2,
    letterSpacing: 1.4,
  },
  chartLegendValue: {
    ...type.mono,
    fontSize: 12,
    fontWeight: "700",
    fontVariant: ["tabular-nums"],
  },

  // Performance / stat group
  statGroup: {
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: colors.border,
    overflow: "hidden",
  },

  // PerfRow — substantial row with icon + value + bar
  perfRow: {
    paddingHorizontal: spacing.lg,
    paddingTop: 16,
    paddingBottom: 16,
    gap: 12,
  },
  perfRowBorder: {
    borderBottomWidth: 0.5,
    borderBottomColor: colors.separator,
  },
  perfRowTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  perfRowLeft: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  perfIconChip: {
    width: 28,
    height: 28,
    borderRadius: radius.sm,
    backgroundColor: colors.bg2,
    borderWidth: 0.5,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  perfLabel: {
    ...type.body,
    fontSize: 14,
    color: colors.text1,
    fontWeight: "500",
  },
  perfValue: {
    ...type.titleM,
    fontSize: 22,
    fontWeight: "800",
    letterSpacing: -0.5,
    fontVariant: ["tabular-nums"],
  },
  perfBarTrack: {
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.bg3,
    overflow: "hidden",
  },
  perfBarFill: { height: "100%", borderRadius: 3 },

  // Fan grid (2-up)
  fanGrid: {
    flexDirection: "row",
    gap: spacing.md,
  },
  fanCard: {
    flex: 1,
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: colors.border,
    padding: spacing.lg,
    gap: 10,
  },
  fanCardTop: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  fanCardIcon: {
    width: 24,
    height: 24,
    borderRadius: radius.sm,
    backgroundColor: colors.accentSoft,
    alignItems: "center",
    justifyContent: "center",
  },
  fanCardValueRow: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 4,
  },
  fanCardRpm: {
    fontSize: 30,
    fontWeight: "800",
    color: colors.text0,
    letterSpacing: -1.2,
    lineHeight: 32,
    fontVariant: ["tabular-nums"],
  },
  fanCardUnit: {
    ...type.caption,
    fontSize: 11,
    color: colors.text2,
    fontWeight: "600",
    paddingBottom: 6,
  },
  fanCardBarTrack: {
    height: 5,
    borderRadius: 2.5,
    backgroundColor: colors.bg3,
    overflow: "hidden",
  },
  fanCardBarFill: {
    height: "100%",
    borderRadius: 2.5,
    backgroundColor: colors.accent,
  },
  fanCardPct: {
    ...type.mono,
    fontSize: 11,
    color: colors.text2,
    fontVariant: ["tabular-nums"],
  },
});
