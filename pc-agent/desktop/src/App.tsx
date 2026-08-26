import { useState, useEffect } from "react";
import { ThemeProvider, useTheme } from "./lib/ThemeContext";
import { AuthProvider, useAuth } from "./lib/AuthContext";
import { useSensors } from "./hooks/useSensors";
import TopNav from "./components/TopNav";
import IconSidebar from "./components/IconSidebar";
import ErrorBoundary from "./components/ErrorBoundary";
import Dashboard from "./pages/Dashboard";
import Profiles from "./pages/Profiles";
import Logs from "./pages/Logs";
import Settings from "./pages/Settings";
import Login from "./pages/Login";
import "./App.css";

export type Page = "dashboard" | "profiles" | "logs" | "settings";

function AppInner() {
  const [page, setPage] = useState<Page>("dashboard");
  const { data, connected } = useSensors();
  const { colors } = useTheme();
  const { session, loading } = useAuth();

  // Keyboard shortcuts. Ctrl+1..4 jumps to a tab; we deliberately don't
  // intercept when the user is typing in an input/textarea (otherwise the
  // ThresholdRow / profile-name inputs would lose number keys). Listening
  // on window so it works regardless of focus.
  useEffect(() => {
    if (!session) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) {
        return;
      }
      if (!(e.ctrlKey || e.metaKey)) return;
      const map: Record<string, Page> = {
        "1": "dashboard",
        "2": "profiles",
        "3": "logs",
        "4": "settings",
      };
      const target = map[e.key];
      if (target) {
        e.preventDefault();
        setPage(target);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [session]);

  if (loading) {
    return (
      <div style={{
        width: "100vw",
        height: "100vh",
        background: colors.bg0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
        gap: 14,
      }}>
        <div style={{
          width: 36, height: 36,
          border: `3px solid ${colors.bg2}`,
          borderTopColor: colors.accent,
          borderRadius: "50%",
          animation: "spin 0.8s linear infinite",
        }} />
        <div style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 11,
          letterSpacing: 1.5,
          color: colors.text3,
          textTransform: "uppercase",
        }}>
          Initializing...
        </div>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }

  if (!session) {
    return <Login />;
  }

  return (
    <div style={{
      display: "flex",
      width: "100vw",
      height: "100vh",
      background: colors.bg0,
      color: colors.text0,
      overflow: "hidden",
    }}>
      <IconSidebar active={page} onNavigate={setPage} />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <TopNav active={page} onNavigate={setPage} connected={connected} session={session} sensorData={data} />
        <main style={{ flex: 1, overflow: "hidden" }}>
          {page === "dashboard" && <Dashboard data={data} connected={connected} />}
          {page === "profiles" && <Profiles sensorData={data} />}
          {page === "logs" && <Logs />}
          {page === "settings" && <Settings />}
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider>
        <AuthProvider>
          <AppInner />
        </AuthProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}