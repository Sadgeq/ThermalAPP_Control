import { useTheme } from "../lib/ThemeContext";
import { ReactNode } from "react";

type Props = {
  label: string;
  value: string;
  unit: string;
  subText: string;
  iconColor: string;
  iconBg: string;
  icon: ReactNode;
  trend?: "up" | "flat" | "down";
};

export default function HeroCard({ label, value, unit, subText, iconColor, iconBg, icon, trend = "flat" }: Props) {
  const { colors } = useTheme();

  return (
    <div style={{
      background: colors.card,
      borderRadius: 14,
      padding: "18px 20px",
      border: `0.5px solid ${colors.cardBorder}`,
      boxShadow: colors.shadow,
      position: "relative",
      overflow: "hidden",
      minWidth: 0,
    }}>
      {/* Icon */}
      <div style={{
        width: 28, height: 28,
        borderRadius: 6,
        background: iconBg,
        color: iconColor,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        marginBottom: 12,
      }}>
        {icon}
      </div>

      {/* Label */}
      <div style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 10,
        fontWeight: 500,
        color: colors.text3,
        letterSpacing: 1,
        textTransform: "uppercase",
        marginBottom: 4,
      }}>
        {label}
      </div>

      {/* Value */}
      <div style={{
        fontFamily: "'Space Grotesk', sans-serif",
        fontSize: 36,
        fontWeight: 700,
        color: colors.text0,
        lineHeight: 1,
        letterSpacing: -1,
      }}>
        {value}
        <span style={{
          fontSize: 15,
          fontWeight: 400,
          color: colors.text2,
          marginLeft: 2,
        }}>
          {unit}
        </span>
      </div>

      {/* Sub text */}
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: 5,
        marginTop: 8,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11,
        color: colors.text3,
      }}>
        {trend === "up" && (
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={colors.accent} strokeWidth="2" strokeLinecap="round">
            <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
          </svg>
        )}
        {trend === "flat" && (
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={colors.text3} strokeWidth="2" strokeLinecap="round">
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
        )}
        {trend === "down" && (
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={colors.warn} strokeWidth="2" strokeLinecap="round">
            <polyline points="23 18 13.5 8.5 8.5 13.5 1 6" />
          </svg>
        )}
        {subText}
      </div>
    </div>
  );
}