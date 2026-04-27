import { useTheme } from "../lib/ThemeContext";
import type { Page } from "../App";
import { getCurrentWindow } from "@tauri-apps/api/window";
type Props = {
  active: Page;
  onNavigate: (page: Page) => void;
  connected: boolean;
  session: any;
};

const TABS: { id: Page; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "profiles", label: "Profiles" },
  { id: "logs", label: "Logs" },
];

export default function TopNav({ active, onNavigate, connected, session }: Props) {
  const { colors, theme, toggle } = useTheme();

  const email = session?.user?.email || "";
  const initial = email ? email.charAt(0).toUpperCase() : "?";

  const appWindow = getCurrentWindow();

  const handleMinimize = () => appWindow.minimize();
  const handleMaximize = () => appWindow.toggleMaximize();
  const handleClose = () => appWindow.close();

  return (
    <div
      data-tauri-drag-region
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        height: 46,
        padding: "0 0 0 16px",
        background: colors.topbar,
        borderBottom: `0.5px solid ${colors.border}`,
        flexShrink: 0,
        userSelect: "none",
        WebkitUserSelect: "none",
      }}
    >
      {/* Brand — draggable */}
      <div
        data-tauri-drag-region
        style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontWeight: 500,
          fontSize: 13,
          letterSpacing: 1.5,
          color: colors.text0,
          display: "flex",
          alignItems: "center",
          gap: 8,
          pointerEvents: "none",
        }}
      >
        <span style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 22, height: 22,
          borderRadius: 5,
          background: colors.accentSoft,
        }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
            stroke={colors.accent} strokeWidth="2.5" strokeLinecap="round">
            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
          </svg>
        </span>
        THERM_OS
      </div>

      {/* Tabs — centered, not draggable */}
      <div style={{
        position: "absolute",
        left: "50%",
        transform: "translateX(-50%)",
        display: "flex",
        gap: 2,
      }}>
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => onNavigate(tab.id)}
            style={{
              padding: "5px 14px",
              borderRadius: 6,
              fontSize: 12,
              fontWeight: 500,
              fontFamily: "'Inter', sans-serif",
              color: active === tab.id ? colors.accent : colors.text2,
              background: active === tab.id ? colors.accentSoft : "transparent",
              border: "none",
              cursor: "pointer",
              transition: "all 0.15s ease",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Right: status + controls + window buttons */}
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        height: "100%",
      }}>
        {/* Connection status */}
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: 5,
          fontSize: 10,
          fontFamily: "'JetBrains Mono', monospace",
          color: connected ? colors.accent : colors.danger,
          marginRight: 4,
        }}>
          <span style={{
            width: 5, height: 5,
            borderRadius: "50%",
            background: connected ? colors.accent : colors.danger,
          }} />
          {connected ? "Online" : "Offline"}
        </div>

        {/* Theme toggle */}
        <button
          onClick={toggle}
          title={theme === "light" ? "Dark mode" : "Light mode"}
          style={iconBtnStyle(colors, false)}
        >
          {theme === "light" ? (
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
            </svg>
          ) : (
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="12" cy="12" r="5" />
              <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
            </svg>
          )}
        </button>

        {/* Settings */}
        <button
          onClick={() => onNavigate("settings")}
          title="Settings"
          style={iconBtnStyle(colors, active === "settings")}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
          </svg>
        </button>

        {/* Avatar */}
        <div style={{
          width: 26, height: 26,
          borderRadius: "50%",
          background: `linear-gradient(135deg, ${colors.accent}, ${colors.accent2})`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 11,
          fontWeight: 600,
          color: "#fff",
          marginRight: 8,
        }}>
          {initial}
        </div>

        {/* ─── Window control separator ─── */}
        <div style={{
          width: 1,
          height: 20,
          background: colors.border2,
          flexShrink: 0,
        }} />

        {/* ─── Window controls ─── */}
        <WinButton onClick={handleMinimize} colors={colors} type="minimize" />
        <WinButton onClick={handleMaximize} colors={colors} type="maximize" />
        <WinButton onClick={handleClose} colors={colors} type="close" />
      </div>
    </div>
  );
}

// ─── Window control button ───
function WinButton({ onClick, colors, type }: {
  onClick: () => void;
  colors: any;
  type: "minimize" | "maximize" | "close";
}) {
  const isClose = type === "close";
  return (
    <button
      onClick={onClick}
      style={{
        width: 40,
        height: 46,
        border: "none",
        background: "transparent",
        color: colors.text2,
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        transition: "background 0.1s ease, color 0.1s ease",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = isClose ? "#E81123" : colors.bg2;
        if (isClose) e.currentTarget.style.color = "#fff";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "transparent";
        e.currentTarget.style.color = colors.text2;
      }}
    >
      {type === "minimize" && (
        <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
          <line x1="0" y1="5.5" x2="11" y2="5.5" stroke="currentColor" strokeWidth="1" />
        </svg>
      )}
      {type === "maximize" && (
        <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
          <rect x="0.5" y="0.5" width="10" height="10" stroke="currentColor" strokeWidth="1" fill="none" />
        </svg>
      )}
      {type === "close" && (
        <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
          <line x1="0" y1="0" x2="11" y2="11" stroke="currentColor" strokeWidth="1" />
          <line x1="11" y1="0" x2="0" y2="11" stroke="currentColor" strokeWidth="1" />
        </svg>
      )}
    </button>
  );
}

// ─── Shared icon button style ───
function iconBtnStyle(colors: any, active: boolean): React.CSSProperties {
  return {
    width: 28, height: 28,
    borderRadius: 6,
    border: `0.5px solid ${active ? colors.accentBorder : colors.border2}`,
    background: active ? colors.accentSoft : "transparent",
    color: active ? colors.accent : colors.text2,
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
  };
}