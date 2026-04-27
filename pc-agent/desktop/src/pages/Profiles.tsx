import { useState, useEffect } from "react";
import { useTheme } from "../lib/ThemeContext";
import { fetchProfiles, activateProfile, ProfileData, FanMode, SensorData } from "../lib/api";

// On 82NL the BIOS exposes 3 thermal policies; map agent profile names to them.
// Profiles still drive agent-side curves/thresholds, but the actual fan response
// is governed by the BIOS mode set here.
const PROFILE_TO_MODE: Record<string, FanMode> = {
  Silent: 1,
  Balanced: 2,
  Gaming: 3,
  Turbo: 3,
};

const MODE_DESCRIPTIONS: Record<string, string> = {
  Silent: "Lowest fan speed caps. Fans ramp gently under load. Best for light tasks and battery life.",
  Balanced: "Default thermal profile. Good for everyday use and most workloads.",
  Gaming: "Higher fan caps, more aggressive cooling. Use for gaming and sustained CPU/GPU load.",
  Turbo: "Same BIOS policy as Gaming on this hardware (82NL exposes 3 modes). Reserved for future differentiation.",
};

type Props = { sensorData: SensorData };

export default function Profiles({ sensorData }: Props) {
  const { colors } = useTheme();
  const [profileData, setProfileData] = useState<ProfileData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activating, setActivating] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError("");
    const data = await fetchProfiles();
    if (data) {
      setProfileData(data);
    } else {
      setError("Could not connect to agent");
    }
    setLoading(false);
  };

  useEffect(() => {
    load();
    // Poll every 5s so profile changes from mobile (or any other client)
    // reflect here without requiring a manual refresh.
    const id = window.setInterval(load, 5000);
    return () => window.clearInterval(id);
  }, []);

  const handleActivate = async (name: string) => {
    if (activating) return;
    setActivating(name);
    // Backend's activate_profile endpoint also sets the BIOS fan mode,
    // so a single call keeps profile + hardware in sync.
    const ok = await activateProfile(name);
    if (ok) {
      setTimeout(() => {
        load();
        setActivating(null);
      }, 500);
    } else {
      setActivating(null);
    }
  };

  const profiles = profileData ? Object.values(profileData.profiles) : [];
  const activeName = profileData?.active || null;

  return (
    <div style={{
      height: "100%",
      display: "flex",
      flexDirection: "column",
      overflow: "hidden",
    }}>
      {/* Header */}
      <div style={{
        padding: "20px 28px 0",
        display: "flex", justifyContent: "space-between", alignItems: "flex-start",
        flexShrink: 0,
      }}>
        <div>
          <div style={{
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 10, fontWeight: 500, color: colors.accent,
            letterSpacing: 2, textTransform: "uppercase", marginBottom: 6,
          }}>Fan control</div>
          <div style={{
            fontFamily: "'Space Grotesk', sans-serif",
            fontSize: 26, fontWeight: 700, color: colors.text0,
          }}>Profiles</div>
        </div>
        {activeName && (
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "6px 14px", borderRadius: 20,
            background: colors.badgeBg, color: colors.badgeText,
            fontSize: 12, fontWeight: 600,
          }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: colors.accent }} />
            Active: {activeName}
          </div>
        )}
      </div>

      {/* Info banner */}
      <div style={{
        margin: "14px 28px 0",
        padding: "10px 14px",
        borderRadius: 10,
        background: colors.infoSoft,
        color: colors.info,
        fontSize: 11,
        lineHeight: 1.5,
        display: "flex",
        alignItems: "center",
        gap: 8,
        flexShrink: 0,
      }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ flexShrink: 0 }}>
          <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
        <span>
          Each profile maps to a BIOS thermal mode (Silent → Quiet, Balanced → Balanced, Gaming/Turbo → Performance). Activating switches the mode via WMI. Fan-curve values are a reference — the BIOS controls actual RPM response under load.
        </span>
      </div>

      {/* Main content */}
      {loading ? (
        <div style={{ color: colors.text3, fontSize: 13, padding: 40, textAlign: "center" }}>
          Loading profiles from agent...
        </div>
      ) : error ? (
        <div style={{
          margin: "20px 28px",
          padding: 20, borderRadius: 14,
          background: colors.dangerSoft, color: colors.danger,
          fontSize: 13, textAlign: "center",
        }}>
          {error}
          <div style={{ marginTop: 10 }}>
            <button onClick={load} style={{
              padding: "6px 16px", borderRadius: 6,
              border: `1px solid ${colors.danger}`,
              background: "transparent", color: colors.danger,
              fontSize: 12, cursor: "pointer",
            }}>Retry</button>
          </div>
        </div>
      ) : (
        <div style={{
          flex: 1, display: "grid",
          gridTemplateColumns: "1fr 280px",
          gap: 16,
          padding: "16px 28px 24px",
          minHeight: 0, overflowY: "auto",
        }}>
          {/* Profile grid — click-to-activate */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(2, 1fr)",
            gap: 12,
            alignContent: "start",
          }}>
            {profiles.map((p) => {
              const isActive = p.name === activeName;
              const isActivating = activating === p.name;
              const mode = PROFILE_TO_MODE[p.name];
              return (
                <button
                  key={p.name}
                  onClick={() => handleActivate(p.name)}
                  disabled={isActive || activating !== null}
                  style={{
                    background: isActive ? colors.accentSoft : colors.card,
                    borderRadius: 12,
                    border: isActive
                      ? `1.5px solid ${colors.accent}`
                      : `0.5px solid ${colors.cardBorder}`,
                    boxShadow: colors.shadow,
                    padding: "18px 20px",
                    textAlign: "left",
                    cursor: isActive ? "default"
                      : activating ? "wait"
                      : "pointer",
                    opacity: activating && !isActivating ? 0.5 : 1,
                    transition: "all 0.15s ease",
                    display: "flex", flexDirection: "column", gap: 8,
                    fontFamily: "'Inter', sans-serif",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div style={{
                      fontFamily: "'Space Grotesk', sans-serif",
                      fontSize: 17, fontWeight: 600,
                      color: isActive ? colors.accent : colors.text0,
                    }}>
                      {p.name}
                    </div>
                    <div style={{
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 9, fontWeight: 500,
                      padding: "2px 7px", borderRadius: 4,
                      background: isActive ? colors.card : colors.bg0,
                      color: isActive ? colors.accent : colors.text2,
                      letterSpacing: 0.6,
                    }}>
                      MODE {mode ?? "—"}
                    </div>
                  </div>

                  <div style={{
                    fontSize: 12, lineHeight: 1.5,
                    color: colors.text2,
                  }}>
                    {MODE_DESCRIPTIONS[p.name] ?? "Custom profile."}
                  </div>

                  <div style={{
                    marginTop: "auto",
                    paddingTop: 10,
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 10, fontWeight: 500,
                    letterSpacing: 0.8, textTransform: "uppercase",
                    color: isActive ? colors.accent
                      : isActivating ? colors.text3
                      : colors.text2,
                  }}>
                    {isActive ? "● Active"
                      : isActivating ? "Activating…"
                      : "Tap to activate"}
                  </div>
                </button>
              );
            })}
          </div>

          {/* Right: Thermal status (live) */}
          <div style={{
            background: colors.card,
            borderRadius: 12,
            border: `0.5px solid ${colors.cardBorder}`,
            boxShadow: colors.shadow,
            padding: 20,
            display: "flex", flexDirection: "column", gap: 14,
            alignSelf: "start",
          }}>
            <div style={{
              fontFamily: "'Space Grotesk', sans-serif",
              fontSize: 15, fontWeight: 600, color: colors.text0,
            }}>
              Thermal status
            </div>

            <StatusRow label="CPU" value={sensorData?.cpu_temp != null ? `${Math.round(sensorData.cpu_temp)} °C` : "—"} colors={colors} />
            <StatusRow label="GPU" value={sensorData?.gpu_temp != null ? `${Math.round(sensorData.gpu_temp)} °C` : "—"} colors={colors} />
            {sensorData?.fan_speeds?.map((f, i) => (
              <StatusRow key={i} label={f.name} value={`${f.rpm.toLocaleString()} RPM`} colors={colors} />
            ))}
            <div style={{ height: 1, background: colors.border, margin: "4px -20px" }} />
            <div style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 10, letterSpacing: 0.8,
              color: colors.text3, textTransform: "uppercase",
            }}>
              Active profile
            </div>
            <div style={{
              fontFamily: "'Space Grotesk', sans-serif",
              fontSize: 18, fontWeight: 600, color: colors.accent,
            }}>
              {activeName ?? "—"}
              {activeName && PROFILE_TO_MODE[activeName] && (
                <span style={{
                  marginLeft: 8, fontSize: 11, color: colors.text3,
                  fontFamily: "'JetBrains Mono', monospace",
                  fontWeight: 500,
                }}>
                  MODE {PROFILE_TO_MODE[activeName]}
                </span>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatusRow({ label, value, colors }: {
  label: string;
  value: string;
  colors: any;
}) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
    }}>
      <span style={{ fontSize: 12, color: colors.text2 }}>{label}</span>
      <span style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 12, fontWeight: 500, color: colors.text0,
      }}>
        {value}
      </span>
    </div>
  );
}