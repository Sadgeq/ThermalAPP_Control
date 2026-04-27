import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  Animated,
  Easing,
  Platform,
  Share,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import * as Haptics from "expo-haptics";
import { supabase } from "@/lib/supabase";
import { colors, radius, spacing, type } from "@/lib/theme";

type CodeState =
  | { kind: "loading" }
  | { kind: "ready"; code: string; expiresAt: Date }
  | { kind: "error"; message: string };

export default function PairScreen() {
  const router = useRouter();
  const [state, setState] = useState<CodeState>({ kind: "loading" });
  const [now, setNow] = useState(Date.now());
  const fade = useRef(new Animated.Value(0)).current;

  const generate = async () => {
    setState({ kind: "loading" });
    try {
      // We pass whichever auth tokens we have. The RPC stores them so the
      // agent can bootstrap a session: prefers refresh_token (long-lived)
      // and falls back to access_token (~1h, requires re-pair). Some OAuth
      // providers don't emit a refresh_token at all, so we can't require it.
      try {
        await supabase.auth.refreshSession();
      } catch {}

      const { data: sessionData } = await supabase.auth.getSession();
      const access = sessionData.session?.access_token;
      const refresh = sessionData.session?.refresh_token;

      if (!access && !refresh) {
        throw new Error("Not signed in. Sign in and try again.");
      }

      const { data, error } = await supabase.rpc("generate_pairing_code", {
        p_refresh_token: refresh || null,
        p_access_token: access || null,
      });
      if (error) throw error;

      const row = Array.isArray(data) ? data[0] : data;
      if (!row?.code) throw new Error("Empty response from server");

      setState({
        kind: "ready",
        code: row.code,
        expiresAt: new Date(row.expires_at),
      });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(
        () => {}
      );
      Animated.timing(fade, {
        toValue: 1,
        duration: 220,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }).start();
    } catch (e: any) {
      setState({ kind: "error", message: e.message ?? "Could not get code" });
    }
  };

  useEffect(() => {
    generate();
  }, []);

  // Tick once a second for the countdown.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const remaining = useMemo(() => {
    if (state.kind !== "ready") return 0;
    return Math.max(0, Math.floor((state.expiresAt.getTime() - now) / 1000));
  }, [state, now]);

  const formatted = state.kind === "ready"
    ? `${state.code.slice(0, 3)}-${state.code.slice(3)}`
    : "------";
  const expired = state.kind === "ready" && remaining === 0;
  const mm = String(Math.floor(remaining / 60)).padStart(2, "0");
  const ss = String(remaining % 60).padStart(2, "0");

  const onShare = async () => {
    if (state.kind !== "ready") return;
    try {
      await Share.share({
        message: `ThermalControl pairing code: ${formatted}`,
      });
    } catch {}
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} hitSlop={12}>
          <Text style={styles.back}>Back</Text>
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Add this PC</Text>
        <View style={{ width: 48 }} />
      </View>

      <View style={styles.body}>
        <Text style={type.eyebrow}>One-time pairing</Text>
        <Text style={styles.lead}>
          Run the ThermalControl agent on your PC, then enter this code when
          asked.
        </Text>

        <Animated.View style={[styles.codeCard, { opacity: state.kind === "ready" ? fade : 1 }]}>
          {state.kind === "loading" ? (
            <ActivityIndicator color={colors.accent} size="large" />
          ) : state.kind === "error" ? (
            <Text style={styles.errorText}>{state.message}</Text>
          ) : (
            <>
              <Text style={[styles.code, expired && styles.codeExpired]}>
                {formatted}
              </Text>
              <Text style={styles.countdown}>
                {expired ? "Expired" : `Expires in ${mm}:${ss}`}
              </Text>
            </>
          )}
        </Animated.View>

        <View style={styles.steps}>
          <Step n="1" t="Install the ThermalControl agent on your PC." />
          <Step n="2" t="Launch it. A console window will appear." />
          <Step n="3" t="Type the 6-character code shown above." />
          <Step n="4" t="Done — you'll see your PC in the Dashboard." />
        </View>

        <View style={styles.actions}>
          <TouchableOpacity
            style={[styles.btn, styles.btnPrimary]}
            onPress={generate}
            disabled={state.kind === "loading"}
            activeOpacity={0.85}
          >
            <Text style={styles.btnPrimaryLabel}>
              {state.kind === "ready" && !expired ? "New code" : "Get code"}
            </Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.btn, styles.btnGhost]}
            onPress={onShare}
            disabled={state.kind !== "ready"}
            activeOpacity={0.85}
          >
            <Text style={styles.btnGhostLabel}>Share</Text>
          </TouchableOpacity>
        </View>
      </View>
    </SafeAreaView>
  );
}

function Step({ n, t }: { n: string; t: string }) {
  return (
    <View style={styles.step}>
      <View style={styles.stepNum}>
        <Text style={styles.stepNumText}>{n}</Text>
      </View>
      <Text style={styles.stepText}>{t}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg0 },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
  },
  back: { ...type.body, color: colors.text2, width: 48 },
  headerTitle: { ...type.titleS, fontSize: 16, fontWeight: "700" },
  body: { flex: 1, padding: spacing.lg, gap: spacing.lg },
  lead: { ...type.body, color: colors.text2, marginBottom: spacing.sm },

  codeCard: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: colors.border,
    paddingVertical: spacing.xxl ?? 28,
    minHeight: 160,
    gap: 8,
  },
  code: {
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace" }),
    fontSize: 48,
    fontWeight: "800",
    letterSpacing: 6,
    color: colors.accent,
  },
  codeExpired: { color: colors.text3 },
  countdown: { ...type.footnote, color: colors.text2, letterSpacing: 1.5 },
  errorText: { ...type.body, color: colors.danger, textAlign: "center" },

  steps: { gap: spacing.sm },
  step: { flexDirection: "row", gap: spacing.md, alignItems: "flex-start" },
  stepNum: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: colors.accentSoft,
    alignItems: "center",
    justifyContent: "center",
  },
  stepNumText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800",
  },
  stepText: { ...type.body, color: colors.text1, flex: 1, lineHeight: 22 },

  actions: { flexDirection: "row", gap: spacing.md, marginTop: "auto" },
  btn: {
    flex: 1,
    paddingVertical: 14,
    borderRadius: radius.lg,
    alignItems: "center",
  },
  btnPrimary: { backgroundColor: colors.accent },
  btnPrimaryLabel: {
    ...type.bodyStrong,
    color: colors.onAccent,
    fontWeight: "800",
  },
  btnGhost: {
    backgroundColor: colors.bg1,
    borderWidth: 0.5,
    borderColor: colors.border,
  },
  btnGhostLabel: { ...type.bodyStrong, color: colors.text0 },
});
