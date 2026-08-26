import { useTheme } from "../lib/ThemeContext";

type Props = {
  name: string;
  percent: number;
  rpm: number;
};

export default function FanBar({ name, percent, rpm }: Props) {
  const { colors } = useTheme();

  const barColor =
    percent > 80 ? colors.warn :
    percent > 50 ? colors.accent :
    colors.accent2;

  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: 12,
    }}>
      <div style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11,
        fontWeight: 500,
        color: colors.text2,
        textTransform: "uppercase",
        letterSpacing: 0.8,
        width: 110,
        flexShrink: 0,
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
      }}>
        {name}
      </div>

      <div style={{
        flex: 1,
        height: 8,
        background: colors.bg1,
        borderRadius: 4,
        overflow: "hidden",
      }}>
        <div style={{
          height: "100%",
          width: `${Math.min(percent, 100)}%`,
          borderRadius: 4,
          background: barColor,
          transition: "width 0.5s ease",
        }} />
      </div>

      <div style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11,
        fontWeight: 500,
        // 0 RPM is meaningful (fan-stop / idle); show it instead of "—".
        // Dimming the color makes it visually clear the fan is parked.
        color: rpm > 0 ? colors.text2 : colors.text3,
        width: 70,
        textAlign: "right",
        flexShrink: 0,
      }}>
        {`${rpm.toLocaleString()} rpm`}
      </div>

      <div style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 12,
        fontWeight: 500,
        color: colors.text0,
        width: 44,
        textAlign: "right",
        flexShrink: 0,
      }}>
        {Math.round(percent)}%
      </div>
    </div>
  );
}