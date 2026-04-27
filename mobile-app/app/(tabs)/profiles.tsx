import React, { useEffect, useState } from "react";
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
};

const PROFILE_TO_MODE: Record<string, number> = {
  Silent: 1,
  Balanced: 2,
  Gaming: 3,
  Turbo: 3,
};

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
      Alert.alert("Failed", e.message);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      fetchProfiles();
    } finally {
      setActivating(null);
    }
  };

  const activeProfile = profiles.find((p) => p.is_active);

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
                {PROFILE_TO_MODE[activeProfile.name] ?? "—"}
              </Text>
            </View>
          </View>
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
              const mode = PROFILE_TO_MODE[p.name];
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
