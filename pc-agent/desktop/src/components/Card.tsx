import { useTheme } from "../lib/ThemeContext";
import { ReactNode } from "react";

type Props = {
  label?: string;
  subLabel?: string;
  children: ReactNode;
  rightHeader?: ReactNode;
  style?: React.CSSProperties;
};

export default function Card({ label, subLabel, children, rightHeader, style }: Props) {
  const { colors } = useTheme();

  return (
    <div style={{
      background: colors.card,
      borderRadius: 14,
      padding: 20,
      border: `0.5px solid ${colors.cardBorder}`,
      boxShadow: colors.shadow,
      ...style,
    }}>
      {(label || rightHeader) && (
        <div style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: subLabel ? 2 : 16,
        }}>
          {label && (
            <div style={{
              fontFamily: "'Space Grotesk', sans-serif",
              fontSize: 15,
              fontWeight: 600,
              color: colors.text0,
            }}>
              {label}
            </div>
          )}
          {rightHeader}
        </div>
      )}
      {subLabel && (
        <div style={{
          fontSize: 12,
          color: colors.text3,
          marginBottom: 16,
        }}>
          {subLabel}
        </div>
      )}
      {children}
    </div>
  );
}