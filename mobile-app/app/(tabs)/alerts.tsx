import React, { useEffect, useMemo, useState, useCallback } from "react";
import {
  View,
  Text,
  SectionList,
  StyleSheet,
  RefreshControl,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import Svg, { Path, Circle, Line } from "react-native-svg";
import { supabase } from "@/lib/supabase";
import { useDevices } from "@/hooks/useDevices";
import { colors, radius, spacing, type } from "@/lib/theme";

type AlertItem = {
  id: string;
  metric: string;
  value: number;
  threshold: number;
  severity: string;
  created_at: string;
};

const SEVERITY_COLORS: Record<string, string> = {
  warning: colors.warn,
  critical: colors.danger,
  info: colors.info,
};

const WarningIcon = ({ color }: { color: string }) => (
  <Svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={color}
    strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <Path d="M12 9v4M12 17h.01" />
    <Path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
  </Svg>
);

const CriticalIcon = ({ color }: { color: string }) => (
  <Svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={color}
    strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <Circle cx="12" cy="12" r="10" />
    <Line x1="12" y1="8" x2="12" y2="12" />
    <Line x1="12" y1="16" x2="12.01" y2="16" />
  </Svg>
);

const InfoIcon = ({ color }: { color: string }) => (
  <Svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={color}
    strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <Circle cx="12" cy="12" r="10" />
    <Line x1="12" y1="16" x2="12" y2="12" />
    <Line x1="12" y1="8" x2="12.01" y2="8" />
  </Svg>
);

const EmptyIcon = ({ color }: { color: string }) => (
  <Svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke={color}
    strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
    <Path d="M22 11.08V12a10 10 0 11-5.93-9.14" />
    <Path d="M22 4L12 14.01l-3-3" />
  </Svg>
);

function severityIcon(severity: string, color: string) {
  if (severity === "critical") return <CriticalIcon color={color} />;
  if (severity === "warning") return <WarningIcon color={color} />;
  return <InfoIcon color={color} />;
}

/* Date bucketing: Today / Yesterday / Earlier */
function dateBucket(iso: string): "Today" | "Yesterday" | "Earlier" {
  const d = new Date(iso);
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfYesterday = new Date(startOfToday);
  startOfYesterday.setDate(startOfToday.getDate() - 1);
  if (d >= startOfToday) return "Today";
  if (d >= startOfYesterday) return "Yesterday";
  return "Earlier";
}

function formatTime(iso: string) {
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "Just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHrs = Math.floor(diffMin / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  return d.toLocaleDateString();
}

export default function AlertsScreen() {
  const { selectedId } = useDevices();
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchAlerts = async () => {
    if (!selectedId) return;
    const { data } = await supabase
      .from("alerts")
      .select("*")
      .eq("device_id", selectedId)
      .order("created_at", { ascending: false })
      .limit(50);
    setAlerts(data || []);
    setLoading(false);
  };

  useEffect(() => {
    fetchAlerts();
    if (!selectedId) return;
    const channel = supabase
      .channel(`alerts-screen:${selectedId}`)
      .on(
        "postgres_changes",
        {
          event: "INSERT",
          schema: "public",
          table: "alerts",
          filter: `device_id=eq.${selectedId}`,
        },
        (payload) => {
          const row = payload.new as AlertItem;
          setAlerts((prev) => [row, ...prev].slice(0, 50));
        }
      )
      .subscribe();
    const pollId = setInterval(fetchAlerts, 10_000);
    return () => {
      supabase.removeChannel(channel);
      clearInterval(pollId);
    };
  }, [selectedId]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await fetchAlerts();
    setRefreshing(false);
  }, [selectedId]);

  // Severity counts (for header strip)
  const counts = useMemo(() => {
    return alerts.reduce(
      (acc, a) => {
        const s = a.severity || "info";
        if (s === "critical") acc.critical++;
        else if (s === "warning") acc.warning++;
        else acc.info++;
        return acc;
      },
      { critical: 0, warning: 0, info: 0 }
    );
  }, [alerts]);

  // Group by date bucket for SectionList
  const sections = useMemo(() => {
    const buckets: Record<string, AlertItem[]> = {
      Today: [],
      Yesterday: [],
      Earlier: [],
    };
    alerts.forEach((a) => {
      buckets[dateBucket(a.created_at)].push(a);
    });
    return (["Today", "Yesterday", "Earlier"] as const)
      .filter((k) => buckets[k].length > 0)
      .map((k) => ({ title: k, data: buckets[k] }));
  }, [alerts]);

  const renderAlert = ({ item, index, section }: any) => {
    const color = SEVERITY_COLORS[item.severity] || colors.text2;
    const last = index === section.data.length - 1;
    return (
      <View style={[styles.row, !last && styles.rowBorder]}>
        <View style={[styles.iconWrap, { backgroundColor: color + "1F" }]}>
          {severityIcon(item.severity, color)}
        </View>
        <View style={styles.rowBody}>
          <View style={styles.rowTop}>
            <Text style={styles.metric}>
              {item.metric.replace(/_/g, " ").toUpperCase()}
            </Text>
            <Text style={styles.time}>{formatTime(item.created_at)}</Text>
          </View>
          <View style={styles.detailRow}>
            <Text style={[styles.value, { color }]}>
              {item.value.toFixed(1)}°C
            </Text>
            <Text style={styles.threshold}>
              {" / threshold "}{item.threshold}°C
            </Text>
          </View>
        </View>
      </View>
    );
  };

  const total = counts.critical + counts.warning + counts.info;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={type.eyebrow}>Thermal events</Text>
        <Text style={styles.title}>Alerts</Text>
      </View>

      {/* Severity counters strip */}
      <View style={styles.countersRow}>
        <CounterPill
          label="Critical"
          count={counts.critical}
          color={colors.danger}
          softBg={colors.dangerSoft}
        />
        <CounterPill
          label="Warning"
          count={counts.warning}
          color={colors.warn}
          softBg={colors.warnSoft}
        />
        <CounterPill
          label="Info"
          count={counts.info}
          color={colors.info}
          softBg={colors.infoSoft}
        />
      </View>

      {total === 0 && !loading ? (
        <View style={styles.empty}>
          <EmptyIcon color={colors.accent} />
          <Text style={styles.emptyTitle}>All clear</Text>
          <Text style={styles.emptyHint}>
            No thermal events recorded yet. Alerts appear when CPU or GPU
            temperature exceeds your configured thresholds.
          </Text>
        </View>
      ) : (
        <SectionList
          sections={sections}
          keyExtractor={(item) => item.id}
          renderItem={renderAlert}
          renderSectionHeader={({ section }) => (
            <View style={styles.sectionHeader}>
              <Text style={type.eyebrow}>{section.title}</Text>
              <Text style={styles.sectionCount}>
                {section.data.length}
              </Text>
            </View>
          )}
          contentContainerStyle={styles.content}
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              tintColor={colors.accent}
            />
          }
          stickySectionHeadersEnabled={false}
          SectionSeparatorComponent={() => <View style={{ height: spacing.md }} />}
          renderSectionFooter={() => <View style={{ height: spacing.md }} />}
        />
      )}
    </SafeAreaView>
  );
}

function CounterPill({
  label,
  count,
  color,
  softBg,
}: {
  label: string;
  count: number;
  color: string;
  softBg: string;
}) {
  const active = count > 0;
  return (
    <View
      style={[
        styles.counterPill,
        active && {
          backgroundColor: softBg,
          borderColor: color + "55",
        },
      ]}
    >
      <Text
        style={[
          styles.counterCount,
          { color: active ? color : colors.text3 },
        ]}
      >
        {count}
      </Text>
      <Text style={styles.counterLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg0 },
  header: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.md,
    paddingBottom: spacing.md,
    gap: 2,
  },
  title: {
    ...type.displayL,
    fontSize: 36,
    lineHeight: 38,
    letterSpacing: -1,
  },

  // Counter pills strip
  countersRow: {
    flexDirection: "row",
    paddingHorizontal: spacing.lg,
    gap: spacing.sm,
    marginBottom: spacing.md,
  },
  counterPill: {
    flex: 1,
    backgroundColor: colors.bg1,
    borderRadius: radius.md,
    borderWidth: 0.5,
    borderColor: colors.border,
    paddingVertical: 12,
    paddingHorizontal: 12,
    alignItems: "flex-start",
    gap: 4,
  },
  counterCount: {
    fontSize: 24,
    fontWeight: "800",
    letterSpacing: -0.8,
    fontVariant: ["tabular-nums"],
  },
  counterLabel: {
    ...type.caption,
    fontSize: 11,
    fontWeight: "600",
    color: colors.text2,
    letterSpacing: 0.3,
  },

  content: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.xxxl,
  },

  sectionHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 4,
    marginBottom: 8,
  },
  sectionCount: {
    ...type.eyebrow,
    color: colors.text3,
  },

  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    paddingVertical: 14,
    paddingHorizontal: spacing.lg,
    backgroundColor: colors.bg1,
    borderWidth: 0.5,
    borderColor: colors.border,
    borderRadius: radius.md,
    marginBottom: 8,
  },
  rowBorder: {},
  iconWrap: {
    width: 36,
    height: 36,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
  },
  rowBody: { flex: 1, gap: 4 },
  rowTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  metric: {
    ...type.eyebrow,
    fontSize: 10,
    color: colors.text1,
  },
  time: {
    ...type.caption,
    fontSize: 11,
    color: colors.text3,
  },
  detailRow: {
    flexDirection: "row",
    alignItems: "baseline",
  },
  value: {
    fontSize: 18,
    fontWeight: "800",
    letterSpacing: -0.4,
    fontVariant: ["tabular-nums"],
  },
  threshold: {
    ...type.footnote,
    fontSize: 12,
    color: colors.text3,
  },

  empty: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: spacing.xxxl,
    gap: spacing.md,
  },
  emptyTitle: {
    ...type.titleM,
    fontSize: 22,
    color: colors.text0,
    marginTop: spacing.sm,
  },
  emptyHint: {
    ...type.footnote,
    textAlign: "center",
    color: colors.text2,
    lineHeight: 18,
  },
});
