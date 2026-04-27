import React from "react";
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
import { useAuth } from "@/lib/auth-context";
import { useDevices } from "@/hooks/useDevices";
import { colors, radius, spacing, type } from "@/lib/theme";

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
          <StatusRow
            label="Status"
            online={!!device?.is_online}
            last
          />
        </Section>

        <Section label="App">
          <Row label="Version" value="1.0.0" />
          <Row label="Agent protocol" value="v3.2" last />
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
