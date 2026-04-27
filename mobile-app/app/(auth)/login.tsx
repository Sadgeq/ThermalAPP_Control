import React, { useState } from "react";
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  useWindowDimensions,
} from "react-native";
import Svg, { Path } from "react-native-svg";
import { supabase } from "@/lib/supabase";
import { colors, radius, spacing, type } from "@/lib/theme";
import * as WebBrowser from "expo-web-browser";
import { makeRedirectUri } from "expo-auth-session";

WebBrowser.maybeCompleteAuthSession();

export default function LoginScreen() {
  const { width } = useWindowDimensions();
  // Cap content width on tablets / very wide phones for readability
  const contentMaxW = Math.min(width - 32, 480);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [error, setError] = useState("");

  const signInWithEmail = async () => {
    setError("");
    if (!email || !password) {
      setError("Email and password required");
      return;
    }
    setLoading(true);
    const { error } = await supabase.auth.signInWithPassword({
      email: email.trim(),
      password,
    });
    setLoading(false);
    if (error) setError(error.message);
  };

  const signInWithGoogle = async () => {
    setError("");
    try {
      setGoogleLoading(true);
      const redirectUrl = makeRedirectUri({ scheme: "thermalcontrol" });
      const { data, error } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: { redirectTo: redirectUrl, skipBrowserRedirect: true },
      });
      if (error) throw error;
      if (data?.url) {
        const result = await WebBrowser.openAuthSessionAsync(data.url, redirectUrl);
        if (result.type === "success" && result.url) {
          const url = new URL(result.url);
          const params = new URLSearchParams(
            url.hash ? url.hash.substring(1) : url.search
          );
          const accessToken = params.get("access_token");
          const refreshToken = params.get("refresh_token");
          if (accessToken) {
            await supabase.auth.setSession({
              access_token: accessToken,
              refresh_token: refreshToken || "",
            });
          }
        }
      }
    } catch (e: any) {
      setError(e.message || "Google sign-in failed");
    } finally {
      setGoogleLoading(false);
    }
  };

  const busy = loading || googleLoading;

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <ScrollView
        contentContainerStyle={styles.scroll}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <View style={[styles.contentWrap, { maxWidth: contentMaxW, width: "100%" }]}>
          {/* Brand */}
          <View style={styles.brand}>
            <View style={styles.logoChip}>
              <Svg width="32" height="32" viewBox="0 0 24 24" fill="none"
                stroke={colors.accent} strokeWidth={2.5}
                strokeLinecap="round" strokeLinejoin="round">
                <Path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
              </Svg>
            </View>
            <Text style={styles.wordmark}>THERM_OS</Text>
          </View>

          {/* Hero copy */}
          <View style={styles.heroBlock}>
            <Text style={styles.heroTitle}>
              Take{"\n"}control of{"\n"}your thermals.
            </Text>
            <Text style={styles.heroSub}>
              Real-time monitoring and BIOS-level fan control,
              from anywhere.
            </Text>
          </View>

          {/* Form (compact) */}
          <View style={styles.formCard}>
            <View style={styles.inputRow}>
              <Text style={styles.inputLabel}>Email</Text>
              <TextInput
                style={styles.input}
                placeholder="you@example.com"
                placeholderTextColor={colors.text3}
                value={email}
                onChangeText={setEmail}
                keyboardType="email-address"
                autoCapitalize="none"
                autoCorrect={false}
                autoComplete="email"
                editable={!busy}
              />
            </View>
            <View style={styles.inputSeparator} />
            <View style={styles.inputRow}>
              <Text style={styles.inputLabel}>Password</Text>
              <TextInput
                style={styles.input}
                placeholder="••••••••"
                placeholderTextColor={colors.text3}
                value={password}
                onChangeText={setPassword}
                secureTextEntry
                autoComplete="password"
                editable={!busy}
                onSubmitEditing={signInWithEmail}
                returnKeyType="go"
              />
            </View>
          </View>

          {error ? (
            <View style={styles.errorCard}>
              <Text style={styles.errorText}>{error}</Text>
            </View>
          ) : null}

          {/* Primary action — pill */}
          <TouchableOpacity
            style={[styles.primaryBtn, busy && styles.btnDisabled]}
            onPress={signInWithEmail}
            disabled={busy}
            activeOpacity={0.85}
          >
            {loading ? (
              <ActivityIndicator size="small" color={colors.onAccent} />
            ) : (
              <Text style={styles.primaryLabel}>Sign in</Text>
            )}
          </TouchableOpacity>

          {/* Google — secondary pill */}
          <TouchableOpacity
            style={[styles.secondaryBtn, busy && styles.btnDisabled]}
            onPress={signInWithGoogle}
            disabled={busy}
            activeOpacity={0.85}
          >
            {googleLoading ? (
              <ActivityIndicator size="small" color={colors.text0} />
            ) : (
              <>
                <Svg width="18" height="18" viewBox="0 0 24 24">
                  <Path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                  <Path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                  <Path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
                  <Path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
                </Svg>
                <Text style={styles.secondaryLabel}>Continue with Google</Text>
              </>
            )}
          </TouchableOpacity>

          <Text style={styles.footer}>
            Same account as the desktop app.
          </Text>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg0 },
  scroll: {
    flexGrow: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: spacing.xxxl,
    paddingHorizontal: spacing.lg,
  },
  contentWrap: { gap: spacing.xl },

  brand: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
    marginBottom: spacing.sm,
  },
  logoChip: {
    width: 56,
    height: 56,
    borderRadius: radius.lg,
    backgroundColor: colors.accentSoft,
    borderWidth: 0.5,
    borderColor: "rgba(61,220,151,0.3)",
    alignItems: "center",
    justifyContent: "center",
  },
  wordmark: {
    ...type.titleM,
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace" }),
    letterSpacing: 4,
    fontWeight: "600",
    fontSize: 18,
  },

  heroBlock: {
    marginTop: spacing.lg,
    marginBottom: spacing.sm,
    gap: spacing.lg,
  },
  heroTitle: {
    ...type.displayXL,
    fontSize: 44,
    lineHeight: 46,
    letterSpacing: -1.6,
  },
  heroSub: {
    ...type.body,
    color: colors.text2,
    fontSize: 15,
    lineHeight: 21,
    maxWidth: 320,
  },

  formCard: {
    backgroundColor: colors.bg1,
    borderRadius: radius.lg,
    borderWidth: 0.5,
    borderColor: colors.border2,
    overflow: "hidden",
    marginTop: spacing.md,
  },
  inputRow: {
    paddingHorizontal: spacing.lg,
    paddingVertical: 12,
  },
  inputLabel: {
    ...type.eyebrow,
    fontSize: 9,
    color: colors.text3,
    marginBottom: 4,
  },
  input: {
    fontFamily: Platform.select({ ios: "-apple-system", default: "System" }),
    fontSize: 16,
    fontWeight: "500",
    color: colors.text0,
    padding: 0,
    margin: 0,
  },
  inputSeparator: {
    height: 0.5,
    backgroundColor: colors.separator,
    marginLeft: spacing.lg,
  },

  errorCard: {
    backgroundColor: colors.dangerSoft,
    borderRadius: radius.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: 10,
    borderWidth: 0.5,
    borderColor: "rgba(250,82,82,0.25)",
  },
  errorText: { ...type.footnote, color: colors.danger },

  primaryBtn: {
    backgroundColor: colors.accent,
    borderRadius: radius.pill,
    paddingVertical: 16,
    alignItems: "center",
    justifyContent: "center",
    minHeight: 54,
  },
  primaryLabel: {
    ...type.bodyStrong,
    color: colors.onAccent,
    fontSize: 16,
    fontWeight: "700",
    letterSpacing: -0.2,
  },

  secondaryBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.md,
    backgroundColor: colors.bg2,
    borderRadius: radius.pill,
    paddingVertical: 16,
    minHeight: 54,
    borderWidth: 0.5,
    borderColor: colors.border2,
  },
  secondaryLabel: {
    ...type.bodyStrong,
    color: colors.text0,
    fontSize: 15,
  },

  btnDisabled: { opacity: 0.55 },

  footer: {
    ...type.footnote,
    textAlign: "center",
    color: colors.text3,
    marginTop: spacing.md,
  },
});
