import { Platform, TextStyle } from "react-native";

/**
 * Industrial-premium dark palette. Inspired by Linear / Things 3 /
 * Bloomberg Terminal. Deep blacks, off-white text, single bold accent.
 */
export const colors = {
  // Backgrounds (low → high elevation)
  bg0: "#070708",          // app background — slightly warm pure-black
  bg1: "#0F0F12",          // cards / sheets
  bg2: "#17171C",          // elevated surfaces, inputs
  bg3: "#212128",          // pressed / hover
  bg4: "#2D2D36",          // borders hint
  card: "#0F0F12",
  cardHover: "#17171C",

  // Text — descending emphasis, tuned for AA on bg0
  text0: "#FAFAFA",
  text1: "#D4D4D9",
  text2: "#8C8C95",
  text3: "#56565E",

  // Accent — emerald with more saturation
  accent: "#3DDC97",
  accent2: "#19C57E",
  accentSoft: "rgba(61,220,151,0.13)",
  accentGlow: "rgba(61,220,151,0.24)",
  onAccent: "#062017",     // text color on accent backgrounds

  // Semantic
  cyan: "#7BB6FF",
  warn: "#F5A524",
  warnSoft: "rgba(245,165,36,0.14)",
  danger: "#FA5252",
  dangerSoft: "rgba(250,82,82,0.14)",
  info: "#7CB2FF",
  infoSoft: "rgba(124,178,255,0.14)",
  purple: "#B197FC",
  pink: "#F783AC",

  // Structural
  border: "rgba(255,255,255,0.06)",
  border2: "rgba(255,255,255,0.10)",
  separator: "rgba(255,255,255,0.06)",
  hairline: "rgba(255,255,255,0.04)",
};

const SYSTEM_FONT = Platform.select({
  ios: "-apple-system",
  default: "System",
});
const MONO_FONT = Platform.select({
  ios: "Menlo",
  default: "monospace",
});

export const fonts = {
  regular: SYSTEM_FONT,
  medium: SYSTEM_FONT,
  bold: SYSTEM_FONT,
  mono: MONO_FONT,
};

/**
 * Typography scale.
 * Display weights (800/900) for hero numbers + screen titles.
 * Tight letter-spacing on display sizes for that "premium app" look.
 * Mono for technical/numeric metadata (eyebrows, RPMs, IDs).
 */
export const type: Record<string, TextStyle> = {
  // Hero — for huge metric values (current CPU temp on dashboard)
  heroNumber: {
    fontFamily: SYSTEM_FONT,
    fontSize: 84,
    fontWeight: "800",
    letterSpacing: -3,
    lineHeight: 84,
    color: colors.text0,
    fontVariant: ["tabular-nums"],
  },

  // Display — title hero, big section headers
  displayXL: {
    fontFamily: SYSTEM_FONT,
    fontSize: 56,
    fontWeight: "800",
    letterSpacing: -2,
    lineHeight: 56,
    color: colors.text0,
  },
  displayL: {
    fontFamily: SYSTEM_FONT,
    fontSize: 38,
    fontWeight: "800",
    letterSpacing: -1.2,
    lineHeight: 40,
    color: colors.text0,
  },
  display: {
    fontFamily: SYSTEM_FONT,
    fontSize: 30,
    fontWeight: "800",
    letterSpacing: -0.8,
    lineHeight: 32,
    color: colors.text0,
  },

  // Title — screen titles, card headers
  titleL: {
    fontFamily: SYSTEM_FONT,
    fontSize: 28,
    fontWeight: "700",
    letterSpacing: -0.5,
    color: colors.text0,
  },
  titleM: {
    fontFamily: SYSTEM_FONT,
    fontSize: 22,
    fontWeight: "700",
    letterSpacing: -0.3,
    color: colors.text0,
  },
  titleS: {
    fontFamily: SYSTEM_FONT,
    fontSize: 17,
    fontWeight: "600",
    letterSpacing: -0.1,
    color: colors.text0,
  },

  // Body
  body: {
    fontFamily: SYSTEM_FONT,
    fontSize: 15,
    fontWeight: "400",
    lineHeight: 21,
    color: colors.text1,
  },
  bodyStrong: {
    fontFamily: SYSTEM_FONT,
    fontSize: 15,
    fontWeight: "600",
    lineHeight: 21,
    color: colors.text0,
  },

  // Smaller text
  footnote: {
    fontFamily: SYSTEM_FONT,
    fontSize: 13,
    fontWeight: "400",
    color: colors.text2,
  },
  caption: {
    fontFamily: SYSTEM_FONT,
    fontSize: 12,
    fontWeight: "500",
    color: colors.text2,
  },

  // Eyebrow — uppercase mono labels for technical metadata
  eyebrow: {
    fontFamily: MONO_FONT,
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 2,
    textTransform: "uppercase" as const,
    color: colors.text2,
  },
  eyebrowAccent: {
    fontFamily: MONO_FONT,
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 2,
    textTransform: "uppercase" as const,
    color: colors.accent,
  },

  // Mono / numeric
  mono: {
    fontFamily: MONO_FONT,
    fontSize: 13,
    fontWeight: "500",
    color: colors.text1,
  },
  monoLarge: {
    fontFamily: MONO_FONT,
    fontSize: 16,
    fontWeight: "600",
    color: colors.text0,
    fontVariant: ["tabular-nums"],
  },
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24,
  xxxl: 32,
  xxxxl: 48,
};

export const radius = {
  sm: 8,
  md: 12,
  lg: 18,
  xl: 24,
  pill: 999,
};

export function tempColor(t: number | null): string {
  if (t == null) return colors.text3;
  if (t < 60) return colors.accent;
  if (t < 80) return colors.warn;
  return colors.danger;
}

export function loadColor(l: number | null): string {
  if (l == null) return colors.text3;
  if (l < 50) return colors.accent;
  if (l < 80) return colors.warn;
  return colors.danger;
}
