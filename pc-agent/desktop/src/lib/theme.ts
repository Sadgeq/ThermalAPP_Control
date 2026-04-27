export type Theme = "light" | "dark";

export const light = {
  // Backgrounds
  bg0: "#F4F3F0",
  bg1: "#ECEAE6",
  bg2: "#E4E2DD",
  bg3: "#D8D6D0",

  // Cards
  card: "#FFFFFF",
  cardHover: "#FAFAF9",
  cardBorder: "rgba(0,0,0,0.09)",

  // Text — tuned for WCAG AA on bg0
  text0: "#111111",
  text1: "#3A3A38",
  text2: "#5A5A54",
  text3: "#7A7A72",

  // Accent — emerald green
  accent: "#00A86B",
  accent2: "#00D484",
  accentSoft: "rgba(0,168,107,0.08)",
  accentBorder: "rgba(0,168,107,0.18)",

  // Semantic — darkened for contrast on warm bg
  danger: "#C53030",
  dangerSoft: "rgba(197,48,48,0.08)",
  warn: "#A16207",
  warnSoft: "rgba(161,98,7,0.08)",
  info: "#2E7DD4",
  infoSoft: "rgba(46,125,212,0.08)",
  cyan: "#06b6d4",
  cyanSoft: "rgba(6,182,212,0.08)",

  // Badge
  badgeBg: "#E6F9F0",
  badgeText: "#0A6640",

  // Borders & shadows
  border: "rgba(0,0,0,0.09)",
  border2: "rgba(0,0,0,0.14)",
  shadow: "0 1px 2px rgba(0,0,0,0.04), 0 2px 8px rgba(0,0,0,0.06)",
  shadowLg: "0 4px 16px rgba(0,0,0,0.08)",

  // Surfaces
  topbar: "#FFFFFF",
  sidebar: "#FFFFFF",
};

export const dark = {
  bg0: "#0C0C0C",
  bg1: "#151515",
  bg2: "#1C1C1C",
  bg3: "#262626",

  card: "#181818",
  cardHover: "#1E1E1E",
  cardBorder: "rgba(255,255,255,0.06)",

  text0: "#EDEDEB",
  text1: "#BFBFBA",
  text2: "#7A7A74",
  text3: "#4A4A46",

  accent: "#00D484",
  accent2: "#00F59B",
  accentSoft: "rgba(0,212,132,0.10)",
  accentBorder: "rgba(0,212,132,0.20)",

  danger: "#F06060",
  dangerSoft: "rgba(240,96,96,0.10)",
  warn: "#E0A020",
  warnSoft: "rgba(224,160,32,0.10)",
  info: "#5B9EF0",
  infoSoft: "rgba(91,158,240,0.10)",
  cyan: "#22D3EE",
  cyanSoft: "rgba(34,211,238,0.10)",

  badgeBg: "rgba(0,212,132,0.12)",
  badgeText: "#00D484",

  border: "rgba(255,255,255,0.06)",
  border2: "rgba(255,255,255,0.10)",
  shadow: "0 1px 3px rgba(0,0,0,0.3), 0 4px 12px rgba(0,0,0,0.2)",
  shadowLg: "0 4px 20px rgba(0,0,0,0.3)",

  topbar: "#121212",
  sidebar: "#101010",
};

export type Colors = typeof light;

export function tempColor(t: number | null, c: Colors): string {
  if (t == null) return c.text3;
  if (t < 50) return c.accent;
  if (t < 70) return c.warn;
  return c.danger;
}

export function loadColor(l: number, c: Colors): string {
  if (l < 50) return c.info;
  if (l < 80) return c.warn;
  return c.danger;
}