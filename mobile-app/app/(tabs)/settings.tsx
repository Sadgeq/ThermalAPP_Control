import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  TouchableOpacity,
  StyleSheet,
  Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import Constants from "expo-constants";
import * as Haptics from "expo-haptics";
import { useAuth } from "@/lib/auth-context";
import { useDevices } from "@/hooks/useDevices";
import { supabase } from "@/lib/supabase";
import { colors, radius, spacing, type } from "@/lib/theme";

// Bounds enforced by the agent + by the SQL CHECK on the alert_settings
// table. Mirroring them here means an out-of-range value is impossible to
// dispatch from the UI.
const ALERT_TEMP_MIN = 50;
const ALERT_TEMP_MAX = 100;

// Mobile app version comes from app.json (managed by Expo) so a build's
// version is what the user actually has, not a hardcoded string that
// drifted from reality.
const MOBILE_APP_VERSION = Constants.expoConfig?.version ?? "—";

export default function SettingsScreen() {
  const router = useRouter();
  const { user, signOut } = useAuth();
  const { devices, selectedId } = useDevices();

  const device = devices.find((d) => d.id === selectedId);

  const handleLogout = () => {
    Alert.alert("Sign out", "Are you sure you want to sign out?", [
      { text: "Cancel", style: "cancel" },
      { text: "Sign out", style: "destructive", onPress: signOut },
    ]);
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <ScrollView
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.header}>
          <Text style={type.eyebrow}>Configuration</Text>
          <Text style={styles.title}>Settings</Text>
        </View>

        {/* Hero user card */}
        <View style={styles.userHero}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>
              {(user?.email?.[0] ?? "?").toUpperCase()}
            </Text>
          </View>
          <View style={styles.userInfo}>
            <Text style={styles.userEmail} numberOfLines={1}>
              {user?.email ?? "Not signed in"}
            </Text>
            <Text style={styles.userId} numberOfLines={1}>
              {user?.id ? `id: ${user.id.slice(0, 8)}…` : ""}
            </Text>
          </View>
        </View>

        <Section label="Account">
          <Row label="Email" value={user?.email ?? "—"} />
          <Row
            label="User ID"
            value={user?.id ? `${user.id.slice(0, 8)}…` : "—"}
            mono
            last
          />
        </Section>

        <Section label="Device">
          <Row label="Name" value={device?.name ?? "—"} />
          <Row
            label="OS"
            value={device?.os_info ?? "—"}
            numberOfLines={1}
          />
          <Row
            label="Hardware ID"
            value={device?.hardware_id ? `${device.hardware_id.slice(0, 8)}…` : "—"}
            mono
          />
          <Row
            label="Driver"
            value={device?.controller || "—"}
            mono
          />
          <Row
            label="Agent version"
            value={device?.app_version || "—"}
            mono
          />
          <StatusRow
            label="Status"
            online={!!device?.is_online}
            last
          />
        </Section>

        {/* Alert thresholds — editable from mobile. The agent reads
            these from the alert_settings table at boot and re-checks on
            each sensor tick. Updating the row triggers cloud realtime
            but the agent's monitoring loop also reads them every cycle,
            so changes apply within ~2 seconds. */}
        {selectedId && (
          <ThresholdsSection deviceId={selectedId} />
        )}

        <Section label="App">
          <Row label="Mobile version" value={MOBILE_APP_VERSION} last />
        </Section>

        <TouchableOpacity
          style={styles.primaryBtn}
          onPress={() => router.push("/pair")}
          activeOpacity={0.85}
        >
          <Text style={styles.primaryLabel}>Add this PC</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.destructiveBtn}
          onPress={handleLogout}
          activeOpacity={0.8}
        >
          <Text style={styles.destructiveLabel}>Sign out</Text>
        </TouchableOpacity>

        <View style={{ height: spacing.xxxl }} />
      </ScrollView>
    </SafeAreaView>
  );
}

// Editor for CPU/GPU alert thresholds. Reads from public.alert_settings
// for the selected device, lets the user adjust each by 1°C, and writes
// back via upsert. The agent reads alert_settings at boot and on each
// monitoring tick, so changes propagate within ~2 seconds.
function ThresholdsSection({ deviceId }: { deviceId: string }) {
  type Row = { metric: "cpu_temp" | "gpu_temp"; threshold: number };
  const [rows, setRows] = useState<Row[]>([
    { metric: "cpu_temp", threshold: 85 },
    { metric: "gpu_temp", threshold: 85 },
  ]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    const fetch = async () => {
      const { data } = await supabase
        .from("alert_settings")
        .select("metric, threshold")
        .eq("device_id", deviceId);
      if (!alive) return;
      if (data && data.length > 0) {
        const cpu = data.find((d: any) => d.metric === "cpu_temp");
        const gpu = data.find((d: any) => d.metric === "gpu_temp");
        setRows([
          { metric: "cpu_temp", threshold: cpu?.threshold ?? 85 },
          { metric: "gpu_temp", threshold: gpu?.threshold ?? 85 },
        ]);
      }
      setLoaded(true);
    };
    fetch();
    return () => { alive = false; };
  }, [deviceId]);

  const update = async (metric: Row["metric"], delta: number) => {
    Haptics.selectionAsync();
    const current = rows.find((r) => r.metric === metric)?.threshold ?? 85;
    const next = Math.max(ALERT_TEMP_MIN, Math.min(ALERT_TEMP_MAX, current + delta));
    if (next === current) return;

    setRows((prev) =>
      prev.map((r) => (r.metric === metric ? { ...r, threshold: next } : r))
    );

    try {
      const { error } = await supabase
        .from("alert_settings")
        .upsert(
          {
            device_id: deviceId,
            metric,
            threshold: next,
            enabled: true,
            cooldown_minutes: 5,
          },
          { onConflict: "device_id,metric" }
        );
      if (error) throw error;
    } catch (e: any) {
      Alert.alert("Couldn't save threshold", e?.message ?? String(e));
    }
  };

  if (!loaded) {
    return (
      <Section label="Praguri alertă">
        <Row label="Loading…" value="" last />
      </Section>
    );
  }

  return (
    <Section label="Praguri alertă (°C)">
      {rows.map((r, i) => (
        <ThresholdRow
          key={r.metric}
          label={r.metric === "cpu_temp" ? "CPU critical" : "GPU critical"}
          value={r.threshold}
          onMinus={() => update(r.metric, -1)}
          onPlus={() => update(r.metric, +1)}
          last={i === rows.length - 1}
        />
      ))}
    </Section>
  );
}

function ThresholdRow({
  label, value, onMinus, onPlus, last,
}: {
  label: string;
  value: number;
  onMinus: () => void;
  onPlus: () => void;
  last?: boolean;
}) {
  return (
    <View style={[styles.row, !last && styles.rowBorder]}>
      <Text style={styles.rowLabel}>{label}</Text>
      <View style={styles.thresholdControls}>
        <TouchableOpacity
          onPress={onMinus}
          style={styles.thresholdStep}
          disabled={value <= ALERT_TEMP_MIN}
          activeOpacity={0.7}
        >
          <Text style={styles.thresholdStepText}>−</Text>
        </TouchableOpacity>
        <Text style={styles.thresholdValue}>{value}°</Text>
        <TouchableOpacity
          onPress={onPlus}
          style={styles.thresholdStep}
          disabled={value >= ALERT_TEMP_MAX}
          activeOpacity={0.7}
        >
          <Text style={styles.thresholdStepText}>+</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={[type.eyebrow, styles.sectionHeader]}>{label}</Text>
      <View style={styles.group}>{children}</View>
    </View>
  );
}

function Row({
  label,
  value,
  mono,
  last,
  numberOfLines,
}: {
  label: string;
  value: string;
  mono?: boolean;
  last?: boolean;
  numberOfLines?: number;
}) {
  return (
    <View style={[styles.row, !last && styles.rowBorder]}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text
        style={[styles.rowValue, mono && styles.rowValueMono]}
        numberOfLines={numberOfLines ?? 1}
      >
        {value}
      </Text>
    </View>
  );
}

function StatusRow({ label, online, last }: { label: string; online: boolean; last?: boolean }) {
  const color = online ? colors.accent : colors.text3;
  return (
    <View style={[styles.row, !last && styles.rowBorder]}>
      <Text style={styles.rowLabel}>{label}</Text>
      <View style={styles.statusChip}>
        <View style={[styles.statusDot, { backgroundColor: color }]} />
        <Text style={[styles.statusText, { color }]}>
          {online ? "Online" : "Offline"}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg0 },
  content: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
  },

  header: {
    paddingVertical: spacing.md,
    gap: 2,
  },
  title: {
    ...type.displayL,
    fontSize: 36,
    lineHeight: 38,
    letterSpacing: -1,
  },

  userHero: {
    marginTop: spacing.md,
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: colors.border,
    padding: spacing.lg,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
  },
  avatar: {
    width: 52,
    height: 52,
    borderRadius: radius.md,
    backgroundColor: colors.accentSoft,
    borderWidth: 0.5,
    borderColor: "rgba(61,220,151,0.3)",
    alignItems: "center",
    justifyContent: "center",
  },
  avatarText: {
    fontSize: 22,
    fontWeight: "800",
    color: colors.accent,
    letterSpacing: -0.4,
  },
  userInfo: { flex: 1, gap: 2 },
  userEmail: {
    ...type.titleS,
    fontSize: 16,
    fontWeight: "700",
  },
  userId: {
    ...type.mono,
    fontSize: 11,
    color: colors.text3,
  },

  section: { marginTop: spacing.xl },
  sectionHeader: {
    marginBottom: spacing.sm,
    paddingHorizontal: spacing.xs,
  },
  group: {
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: colors.border,
    overflow: "hidden",
  },

  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: spacing.lg,
    paddingVertical: 14,
    gap: spacing.md,
  },
  rowBorder: {
    borderBottomWidth: 0.5,
    borderBottomColor: colors.separator,
  },
  rowLabel: {
    ...type.body,
    color: colors.text1,
    fontWeight: "500",
  },
  rowValue: {
    ...type.body,
    color: colors.text0,
    flexShrink: 1,
    textAlign: "right",
  },
  rowValueMono: {
    ...type.mono,
    color: colors.text2,
  },
  thresholdControls: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
  },
  thresholdStep: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: colors.bg2,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 0.5,
    borderColor: "rgba(255,255,255,0.08)",
  },
  thresholdStepText: {
    fontSize: 18,
    color: colors.text0,
    fontWeight: "300",
    lineHeight: 20,
  },
  thresholdValue: {
    fontSize: 16,
    fontWeight: "700",
    color: colors.accent,
    minWidth: 44,
    textAlign: "center",
  },

  statusChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  statusDot: { width: 6, height: 6, borderRadius: 3 },
  statusText: {
    ...type.footnote,
    fontWeight: "600",
  },

  primaryBtn: {
    marginTop: spacing.xl,
    backgroundColor: colors.accent,
    borderRadius: radius.lg,
    paddingVertical: 14,
    alignItems: "center",
  },
  primaryLabel: {
    ...type.bodyStrong,
    color: colors.onAccent,
    fontWeight: "800",
  },

  destructiveBtn: {
    marginTop: spacing.md,
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: colors.border,
    paddingVertical: 14,
    alignItems: "center",
  },
  destructiveLabel: {
    ...type.bodyStrong,
    color: colors.danger,
  },
});
