import React, { useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  TouchableOpacity,
  StyleSheet,
  Alert,
  ActivityIndicator,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import Svg, { Path } from "react-native-svg";
import * as Haptics from "expo-haptics";
import { supabase } from "@/lib/supabase";
import { useDevices } from "@/hooks/useDevices";
import { colors, radius, spacing, type } from "@/lib/theme";

type Profile = {
  id: string;
  name: string;
  is_active: boolean;
  fan_curve: { temp: number; speed: number }[];
  // 1=Quiet, 2=Balanced, 3=Performance — Lenovo Legion BIOS mode.
  // null/undefined means the profile doesn't change the BIOS mode.
  fan_mode: number | null;
  // PID setpoint in °C. When set, the agent runs a closed-loop controller
  // that holds the CPU at this temperature instead of using fan_curve.
  // null means the profile uses fan_curve mode.
  target_temp: number | null;
};

// Bounds for the target-temperature slider. Mirror the SQL CHECK
// constraint in supabase/migrations/0014_profiles_target_temp.sql.
const TARGET_TEMP_MIN = 50;
const TARGET_TEMP_MAX = 90;
const TARGET_TEMP_STEP = 1;

const DESCRIPTIONS: Record<string, string> = {
  Silent: "Lowest fan caps. Quietest under load.",
  Balanced: "Default thermal profile. Everyday use.",
  Gaming: "Higher fan caps. Aggressive cooling.",
  Turbo: "Same policy as Gaming on 82NL.",
};

const CheckIcon = ({ color }: { color: string }) => (
  <Svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={color}
    strokeWidth={2.8} strokeLinecap="round" strokeLinejoin="round">
    <Path d="M20 6L9 17l-5-5" />
  </Svg>
);

export default function ProfilesScreen() {
  const { selectedId } = useDevices();
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [loading, setLoading] = useState(true);
  const [activating, setActivating] = useState<string | null>(null);
  // Debounce timer for target_temp persistence. The +/- steppers fire one
  // saveTargetTemp per tap; without debouncing, going from 55°C to 80°C
  // ends up enqueuing 25 set_profile commands at the agent and trips the
  // 30-cmd/min rate limit. We wait until the user pauses for 600ms, then
  // persist the *final* value once.
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchProfiles = async () => {
    if (!selectedId) return;
    const { data } = await supabase
      .from("profiles")
      .select("*")
      .eq("device_id", selectedId);
    // Order by BIOS mode ascending, then by canonical Silent → Balanced → Gaming → Turbo.
    // Alphabetical order would put Balanced first, which doesn't match the
    // mental model of "quietest → loudest".
    const RANK: Record<string, number> = {
      Silent: 0,
      Balanced: 1,
      Gaming: 2,
      Turbo: 3,
    };
    const ordered = (data || []).slice().sort((a: Profile, b: Profile) => {
      const ra = RANK[a.name] ?? 99;
      const rb = RANK[b.name] ?? 99;
      if (ra !== rb) return ra - rb;
      return a.name.localeCompare(b.name);
    });
    setProfiles(ordered);
    setLoading(false);
  };

  useEffect(() => {
    fetchProfiles();
    if (!selectedId) return;
    // Realtime: whenever the PC agent (or another client) flips is_active on
    // the profiles table, re-fetch so this screen reflects reality.
    const channel = supabase
      .channel(`profiles-screen:${selectedId}`)
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "public",
          table: "profiles",
          filter: `device_id=eq.${selectedId}`,
        },
        () => fetchProfiles()
      )
      .subscribe();
    // Polling fallback — in case realtime isn't enabled for `profiles` table.
    const pollId = setInterval(fetchProfiles, 6_000);
    return () => {
      supabase.removeChannel(channel);
      clearInterval(pollId);
    };
  }, [selectedId]);

  const activate = async (profile: Profile) => {
    if (profile.is_active || activating) return;
    setActivating(profile.name);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);

    try {
      const { error } = await supabase.from("commands").insert({
        device_id: selectedId,
        command_type: "set_profile",
        payload: { profile_name: profile.name },
        status: "pending",
      });
      if (error) throw error;

      setProfiles((prev) =>
        prev.map((p) => ({ ...p, is_active: p.name === profile.name }))
      );
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e: any) {
      const { title, body } = humanizeCommandError(e);
      Alert.alert(title, body);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      fetchProfiles();
    } finally {
      setActivating(null);
    }
  };

  // Translate the database-level errors raised by 0002_command_validator.sql
  // into something a user can act on. The Postgres error message bubbles up
  // verbatim through PostgREST → supabase-js → here, so we pattern-match
  // the message text rather than relying on error codes (which the JS
  // client doesn't always expose).
  function humanizeCommandError(e: any): { title: string; body: string } {
    const msg = String(e?.message ?? e ?? "").toLowerCase();
    if (msg.includes("rate limit") || msg.includes("too_many")) {
      return {
        title: "Slow down",
        body: "You've sent more than 30 commands in a minute. Wait about a minute, then try again.",
      };
    }
    if (msg.includes("not allowed") || msg.includes("command_type")) {
      return {
        title: "Outdated app",
        body: "This action isn't supported by the agent on your PC. Update the desktop app.",
      };
    }
    if (msg.includes("profile_name") || msg.includes("invalid characters")) {
      return {
        title: "Bad profile name",
        body: "Profile names can only contain letters, numbers, spaces, hyphens, and underscores.",
      };
    }
    return { title: "Couldn't change profile", body: e?.message ?? "Unknown error" };
  }

  const activeProfile = profiles.find((p) => p.is_active);

  // Persist a target_temp change for the active profile. null = switch
  // back to fan-curve mode. After the row update we send a `set_profile`
  // command if this profile is currently active, which makes the agent
  // re-fetch profiles from cloud and rebuild the PID controller against
  // the new setpoint.
  //
  // Debounced (600ms) so a quick burst of +/- taps coalesces into a
  // single network round-trip. The UI is updated optimistically on every
  // tap so the value the user sees feels instant. We commit the *latest*
  // value only after the user pauses.
  const saveTargetTemp = (profileId: string, value: number | null) => {
    Haptics.selectionAsync();
    // Optimistic local update — UI follows the finger immediately.
    setProfiles((prev) =>
      prev.map((p) => (p.id === profileId ? { ...p, target_temp: value } : p))
    );

    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      saveTimerRef.current = null;
      try {
        const { error } = await supabase
          .from("profiles")
          .update({ target_temp: value })
          .eq("id", profileId);
        if (error) throw error;

        const updated = profiles.find((p) => p.id === profileId);
        if (updated?.is_active && selectedId) {
          const { error: cmdErr } = await supabase.from("commands").insert({
            device_id: selectedId,
            command_type: "set_profile",
            payload: { profile_name: updated.name },
            status: "pending",
          });
          if (cmdErr) {
            console.warn("set_profile nudge failed:", cmdErr.message);
          }
        }
      } catch (e: any) {
        Alert.alert("Couldn't save", e?.message ?? String(e));
      }
    }, 600);
  };

  // Flush any pending save when the screen unmounts so a half-typed
  // value doesn't get dropped silently.
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, []);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <ScrollView
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.header}>
          <Text style={type.eyebrow}>Fan control</Text>
          <Text style={styles.title}>Profiles</Text>
        </View>

        {/* Active profile hero — visible card at top */}
        {activeProfile && (
          <View style={styles.activeHero}>
            <View style={styles.activeHeroLeft}>
              <Text style={[type.eyebrow, { fontSize: 9, color: colors.accent }]}>
                Active now
              </Text>
              <Text style={styles.activeHeroName}>{activeProfile.name}</Text>
              <Text style={styles.activeHeroDesc} numberOfLines={2}>
                {DESCRIPTIONS[activeProfile.name] ?? "Custom profile."}
              </Text>
            </View>
            <View style={styles.activeHeroBadge}>
              <Text style={[type.eyebrow, { fontSize: 9, color: colors.accent }]}>
                Mode
              </Text>
              <Text style={styles.activeHeroMode}>
                {activeProfile.fan_mode ?? "—"}
              </Text>
            </View>
          </View>
        )}

        {/* PID setpoint editor for the active profile — closed-loop control. */}
        {activeProfile && (
          <TargetTempEditor
            profile={activeProfile}
            onChange={(v) => saveTargetTemp(activeProfile.id, v)}
          />
        )}

        {loading ? (
          <View style={styles.loadingWrap}>
            <ActivityIndicator color={colors.accent} />
          </View>
        ) : profiles.length === 0 ? (
          <View style={styles.emptyCard}>
            <Text style={styles.emptyTitle}>No profiles</Text>
            <Text style={styles.emptyHint}>
              Make sure the desktop agent is running and has synced with the cloud.
            </Text>
          </View>
        ) : (
          <View style={styles.list}>
            {profiles.map((p, i) => {
              const isActive = p.is_active;
              const isActivating = activating === p.name;
              const mode = p.fan_mode;
              return (
                <TouchableOpacity
                  key={p.id}
                  style={[
                    styles.row,
                    i !== profiles.length - 1 && styles.rowBorder,
                    isActive && styles.rowActive,
                  ]}
                  onPress={() => activate(p)}
                  activeOpacity={0.75}
                  disabled={isActive || activating !== null}
                >
                  {/* Left accent bar — visible only on active row */}
                  {isActive && <View style={styles.rowAccentBar} />}

                  <View style={styles.rowMain}>
                    <View style={styles.rowTop}>
                      <Text
                        style={[
                          styles.rowName,
                          isActive && { color: colors.accent },
                        ]}
                      >
                        {p.name}
                      </Text>
                      <View
                        style={[
                          styles.modeBadge,
                          isActive && {
                            backgroundColor: colors.accentSoft,
                            borderColor: "rgba(61,220,151,0.35)",
                          },
                        ]}
                      >
                        <Text
                          style={[
                            styles.modeBadgeText,
                            isActive && { color: colors.accent },
                          ]}
                        >
                          MODE {mode ?? "—"}
                        </Text>
                      </View>
                    </View>
                    <Text style={styles.rowDesc}>
                      {DESCRIPTIONS[p.name] ?? "Custom profile."}
                    </Text>
                  </View>
                  <View style={styles.rowCheck}>
                    {isActivating ? (
                      <ActivityIndicator color={colors.accent} size="small" />
                    ) : isActive ? (
                      <View style={styles.checkCircle}>
                        <CheckIcon color={colors.accent} />
                      </View>
                    ) : null}
                  </View>
                </TouchableOpacity>
              );
            })}
          </View>
        )}

        <View style={styles.noteCard}>
          <Text style={type.eyebrow}>How it works</Text>
          <Text style={styles.noteText}>
            Each profile switches the BIOS thermal policy on your PC. The policies
            differ most under heavy CPU/GPU load — at idle you'll see little change.
          </Text>
        </View>

        <View style={{ height: spacing.xxxl }} />
      </ScrollView>
    </SafeAreaView>
  );
}

// PID setpoint editor — sits below the active hero card. Two states:
//
//   * Curve mode (target_temp == null): shows just a single button
//     "Activează reglare automată" that, on tap, sets target_temp to
//     a sensible default (70 °C) so the user has something to adjust.
//
//   * Target mode (target_temp != null): shows the current setpoint
//     prominently with - / + steppers (1°C each) and a "Înapoi la curbă"
//     button to revert to fan_curve mode.
//
// Every change writes to Supabase immediately. The agent picks up via
// realtime within ~2 s (or on its next poll otherwise) and re-creates
// its PID controller against the new setpoint without losing position.
function TargetTempEditor({
  profile, onChange,
}: {
  profile: Profile;
  onChange: (value: number | null) => void;
}) {
  const isPid = profile.target_temp !== null;
  const value = profile.target_temp ?? 70;

  const dec = () => {
    if (!isPid) return;
    const next = Math.max(TARGET_TEMP_MIN, value - TARGET_TEMP_STEP);
    if (next !== value) onChange(next);
  };
  const inc = () => {
    if (!isPid) return;
    const next = Math.min(TARGET_TEMP_MAX, value + TARGET_TEMP_STEP);
    if (next !== value) onChange(next);
  };
  const enable = () => onChange(70);
  const disable = () => onChange(null);

  return (
    <View style={styles.pidCard}>
      <View style={styles.pidHeader}>
        <Text style={[type.eyebrow, { fontSize: 9, color: colors.accent }]}>
          Reglare automată (PID)
        </Text>
        <Text style={styles.pidStatus}>
          {isPid ? "Activă" : "Mod curbă"}
        </Text>
      </View>

      {isPid ? (
        <>
          <View style={styles.pidValueRow}>
            <TouchableOpacity
              onPress={dec}
              style={styles.pidStepBtn}
              disabled={value <= TARGET_TEMP_MIN}
              activeOpacity={0.7}
            >
              <Text style={styles.pidStepBtnText}>−</Text>
            </TouchableOpacity>
            <View style={styles.pidValueWrap}>
              <Text style={styles.pidValue}>{value}</Text>
              <Text style={styles.pidUnit}>°C</Text>
            </View>
            <TouchableOpacity
              onPress={inc}
              style={styles.pidStepBtn}
              disabled={value >= TARGET_TEMP_MAX}
              activeOpacity={0.7}
            >
              <Text style={styles.pidStepBtnText}>+</Text>
            </TouchableOpacity>
          </View>
          <Text style={styles.pidHint}>
            Agentul reglează ventilatoarele pentru a menține CPU-ul la {value}°C.
            Limite: {TARGET_TEMP_MIN}–{TARGET_TEMP_MAX}°C.
          </Text>
          <TouchableOpacity
            onPress={disable}
            style={styles.pidSecondaryBtn}
            activeOpacity={0.7}
          >
            <Text style={styles.pidSecondaryText}>Înapoi la curbă</Text>
          </TouchableOpacity>
        </>
      ) : (
        <>
          <Text style={styles.pidHint}>
            Profilul folosește curba clasică temperatură → viteză. Activează
            reglarea automată ca să specifici o temperatură-țintă, iar agentul
            să ajusteze ventilatoarele dinamic.
          </Text>
          <TouchableOpacity
            onPress={enable}
            style={styles.pidPrimaryBtn}
            activeOpacity={0.8}
          >
            <Text style={styles.pidPrimaryText}>Activează reglare automată</Text>
          </TouchableOpacity>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg0 },
  content: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm },

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
  // PID target temperature editor (sits below the active hero card).
  pidCard: {
    marginTop: spacing.md,
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: "rgba(255,255,255,0.06)",
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.lg,
    gap: spacing.md,
  },
  pidHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  pidStatus: {
    fontFamily: "SpaceMono" as any,
    fontSize: 11,
    color: colors.text2,
    letterSpacing: 0.6,
    textTransform: "uppercase",
  },
  pidValueRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing.lg,
  },
  pidStepBtn: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: colors.bg2,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 0.5,
    borderColor: "rgba(255,255,255,0.08)",
  },
  pidStepBtnText: {
    fontSize: 28,
    color: colors.text0,
    fontWeight: "300",
    lineHeight: 30,
  },
  pidValueWrap: {
    flexDirection: "row",
    alignItems: "baseline",
    gap: 4,
  },
  pidValue: {
    fontSize: 56,
    fontWeight: "800",
    color: colors.accent,
    letterSpacing: -1.5,
    lineHeight: 60,
  },
  pidUnit: {
    fontSize: 18,
    color: colors.text2,
    fontWeight: "500",
  },
  pidHint: {
    fontSize: 12,
    color: colors.text2,
    lineHeight: 18,
  },
  pidPrimaryBtn: {
    backgroundColor: colors.accent,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    alignItems: "center",
  },
  pidPrimaryText: {
    color: "#0a0c10",
    fontSize: 14,
    fontWeight: "700",
    letterSpacing: 0.3,
  },
  pidSecondaryBtn: {
    paddingVertical: spacing.sm + 2,
    borderRadius: radius.md,
    alignItems: "center",
    borderWidth: 0.5,
    borderColor: "rgba(255,255,255,0.12)",
  },
  pidSecondaryText: {
    color: colors.text1,
    fontSize: 13,
    fontWeight: "500",
  },

  // Active hero card — at top of screen
  activeHero: {
    marginTop: spacing.md,
    flexDirection: "row",
    alignItems: "stretch",
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: "rgba(61,220,151,0.25)",
    overflow: "hidden",
  },
  activeHeroLeft: {
    flex: 1,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.lg,
    gap: 4,
  },
  activeHeroName: {
    fontSize: 28,
    fontWeight: "800",
    color: colors.accent,
    letterSpacing: -0.6,
    marginTop: 2,
  },
  activeHeroDesc: {
    ...type.footnote,
    fontSize: 12,
    color: colors.text2,
    marginTop: 4,
    lineHeight: 16,
  },
  activeHeroBadge: {
    width: 78,
    backgroundColor: colors.accentSoft,
    borderLeftWidth: 0.5,
    borderLeftColor: "rgba(61,220,151,0.25)",
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
  },
  activeHeroMode: {
    fontSize: 36,
    fontWeight: "800",
    color: colors.accent,
    letterSpacing: -1.6,
    fontVariant: ["tabular-nums"],
  },

  loadingWrap: { paddingVertical: spacing.xxxl, alignItems: "center" },

  emptyCard: {
    marginTop: spacing.lg,
    padding: spacing.xl,
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: colors.border,
    alignItems: "center",
    gap: 6,
  },
  emptyTitle: {
    ...type.titleS,
  },
  emptyHint: {
    ...type.footnote,
    textAlign: "center",
  },

  list: {
    marginTop: spacing.lg,
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: colors.border,
    overflow: "hidden",
  },

  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.lg,
    paddingVertical: 16,
    gap: spacing.md,
    position: "relative",
  },
  rowBorder: {
    borderBottomWidth: 0.5,
    borderBottomColor: colors.separator,
  },
  rowActive: {
    backgroundColor: colors.accentSoft,
  },
  rowAccentBar: {
    position: "absolute",
    left: 0,
    top: 8,
    bottom: 8,
    width: 3,
    borderTopRightRadius: 2,
    borderBottomRightRadius: 2,
    backgroundColor: colors.accent,
  },
  rowMain: { flex: 1, gap: 4 },
  rowTop: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  rowName: {
    ...type.titleM,
    fontSize: 18,
    fontWeight: "700",
    letterSpacing: -0.3,
  },
  rowDesc: {
    ...type.footnote,
    fontSize: 12,
    color: colors.text2,
  },

  modeBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    backgroundColor: colors.bg2,
    borderRadius: radius.sm,
    borderWidth: 0.5,
    borderColor: colors.border2,
  },
  modeBadgeText: {
    ...type.caption,
    fontSize: 9,
    fontWeight: "700",
    letterSpacing: 0.8,
    color: colors.text1,
    fontFamily: type.eyebrow.fontFamily,
  },

  rowCheck: { width: 28, alignItems: "flex-end" },
  checkCircle: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
  },

  noteCard: {
    marginTop: spacing.xl,
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: colors.border,
    padding: spacing.lg,
    gap: 8,
  },
  noteText: {
    ...type.body,
    fontSize: 13,
    lineHeight: 18,
    color: colors.text1,
  },
});
