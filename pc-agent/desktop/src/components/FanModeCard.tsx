import { useEffect, useState } from "react";
import { useTheme } from "../lib/ThemeContext";
import Card from "./Card";
import { fetchFanMode, setFanMode, FanMode } from "../lib/api";

const MODES: { id: FanMode; label: string; desc: string }[] = [
  { id: 1, label: "Quiet", desc: "Lowest fan speed caps" },
  { id: 2, label: "Balanced", desc: "Default thermal profile" },
  { id: 3, label: "Performance", desc: "Max fan headroom" },
];

export default function FanModeCard() {
  const { colors } = useTheme();
  const [current, setCurrent] = useState<FanMode | null>(null);
  const [supported, setSupported] = useState<boolean>(true);
  const [busy, setBusy] = useState<FanMode | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      const s = await fetchFanMode();
      if (!alive) return;
      setSupported(s.supported);
      setCurrent((s.mode as FanMode) ?? null);
    };
    poll();
    const id = window.setInterval(poll, 5000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);

  const handleClick = async (m: FanMode) => {
    if (busy || m === current) return;
    setBusy(m);
    setError(null);
    const ok = await setFanMode(m);
    setBusy(null);
    if (ok) {
      setCurrent(m);
    } else {
      setError("Mode change failed — BIOS rejected or backend unavailable");
    }
  };

  if (!supported) {
    return (
      <Card label="Fan Mode" subLabel="Not available on this hardware">
        <div style={{ fontSize: 12, color: colors.text3 }}>
          BIOS thermal-mode control is not exposed by this backend.
        </div>
      </Card>
    );
  }

  return (
    <Card
      label="Fan Mode"
      subLabel="BIOS thermal policy — affects behavior under load"
      rightHeader={
        <div style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 10,
          color: colors.text3,
          letterSpacing: 1,
          textTransform: "uppercase",
        }}>
          mode {current ?? "—"}
        </div>
      }
    >
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
        {MODES.map((m) => {
          const active = current === m.id;
          const pending = busy === m.id;
          return (
            <button
              key={m.id}
              onClick={() => handleClick(m.id)}
              disabled={busy !== null}
              style={{
                padding: "12px 14px",
                borderRadius: 10,
                border: `1px solid ${active ? colors.accentBorder : colors.border2}`,
                background: active ? colors.accentSoft : "transparent",
                color: active ? colors.accent : colors.text1,
                cursor: busy ? "wait" : active ? "default" : "pointer",
                opacity: busy && !pending ? 0.5 : 1,
                transition: "all 0.15s ease",
                display: "flex",
                flexDirection: "column",
                alignItems: "flex-start",
                gap: 4,
                textAlign: "left",
                fontFamily: "'Inter', sans-serif",
              }}
            >
              <div style={{
                fontFamily: "'Space Grotesk', sans-serif",
                fontSize: 14,
                fontWeight: 600,
              }}>
                {m.label}
                {pending && (
                  <span style={{
                    marginLeft: 8, fontSize: 11, color: colors.text3,
                    fontFamily: "'JetBrains Mono', monospace",
                  }}>
                    …
                  </span>
                )}
              </div>
              <div style={{ fontSize: 11, color: colors.text3 }}>
                {m.desc}
              </div>
            </button>
          );
        })}
      </div>
      {error && (
        <div style={{
          marginTop: 10, padding: "8px 10px",
          borderRadius: 6, background: colors.dangerSoft,
          color: colors.danger, fontSize: 11,
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          {error}
        </div>
      )}
    </Card>
  );
}
