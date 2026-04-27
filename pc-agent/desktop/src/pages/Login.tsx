import { useState } from "react";
import { useTheme } from "../lib/ThemeContext";
import { useAuth } from "../lib/AuthContext";
import { signInWithGoogle } from "../lib/supabase";

type Tab = "signin" | "register";

export default function Login() {
  const { colors, theme, toggle } = useTheme();
  const { signIn, signUp } = useAuth();
  const [tab, setTab] = useState<Tab>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);

  const handleSubmit = async () => {
    if (!email || !password) {
      setError("Please fill in all fields");
      return;
    }
    setError("");
    setLoading(true);
    try {
      if (tab === "signin") {
        await signIn(email, password);
      } else {
        await signUp(email, password);
      }
    } catch (e: any) {
      setError(e?.message || "Authentication failed");
    }
    setLoading(false);
  };

  const handleGoogle = async () => {
    setError("");
    setGoogleLoading(true);
    try {
      await signInWithGoogle();
      // AuthContext's onAuthStateChange will now fire and session will populate.
    } catch (e: any) {
      setError(e?.message || "Google sign-in failed");
    }
    setGoogleLoading(false);
  };

  return (
    <div style={{
      width: "100vw",
      height: "100vh",
      background: colors.bg0,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      flexDirection: "column",
    }}>
      {/* Card */}
      <div style={{
        width: 400,
        background: colors.card,
        borderRadius: 18,
        padding: "40px 36px",
        border: `0.5px solid ${colors.cardBorder}`,
        boxShadow: colors.shadowLg,
      }}>
        {/* Brand */}
        <div style={{ textAlign: "center", marginBottom: 32 }}>
          <div style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 44, height: 44,
            borderRadius: 12,
            background: colors.accentSoft,
            marginBottom: 14,
          }}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
              stroke={colors.accent} strokeWidth="2.5" strokeLinecap="round">
              <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
            </svg>
          </div>
          <div style={{
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 16,
            fontWeight: 500,
            letterSpacing: 2,
            color: colors.text0,
          }}>
            THERM_OS
          </div>
          <div style={{
            fontSize: 13,
            color: colors.text3,
            marginTop: 6,
          }}>
            Monitor & control your system thermals
          </div>
        </div>

        {/* Tabs */}
        <div style={{
          display: "flex",
          gap: 2,
          background: colors.bg1,
          borderRadius: 8,
          padding: 3,
          marginBottom: 24,
        }}>
          <button
            onClick={() => { setTab("signin"); setError(""); }}
            style={{
              flex: 1,
              padding: "8px 0",
              borderRadius: 6,
              border: "none",
              cursor: "pointer",
              fontSize: 13,
              fontWeight: 500,
              fontFamily: "'Inter', sans-serif",
              color: tab === "signin" ? colors.text0 : colors.text3,
              background: tab === "signin" ? colors.card : "transparent",
              boxShadow: tab === "signin" ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
              transition: "all 0.15s",
            }}
          >
            Sign in
          </button>
          <button
            onClick={() => { setTab("register"); setError(""); }}
            style={{
              flex: 1,
              padding: "8px 0",
              borderRadius: 6,
              border: "none",
              cursor: "pointer",
              fontSize: 13,
              fontWeight: 500,
              fontFamily: "'Inter', sans-serif",
              color: tab === "register" ? colors.text0 : colors.text3,
              background: tab === "register" ? colors.card : "transparent",
              boxShadow: tab === "register" ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
              transition: "all 0.15s",
            }}
          >
            Register
          </button>
        </div>

        {/* Google sign-in */}
        <button
          onClick={handleGoogle}
          disabled={googleLoading || loading}
          style={{
            width: "100%",
            padding: "11px 0",
            borderRadius: 8,
            border: `0.5px solid ${colors.border2}`,
            background: colors.card,
            cursor: googleLoading ? "wait" : "pointer",
            fontSize: 13,
            fontWeight: 500,
            fontFamily: "'Inter', sans-serif",
            color: colors.text0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 10,
            marginBottom: 18,
            transition: "all 0.15s",
          }}
        >
          {/* Google "G" icon */}
          <svg width="16" height="16" viewBox="0 0 24 24" style={{ flexShrink: 0 }}>
            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
            <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
          </svg>
          {googleLoading ? "Waiting for Google..." : "Sign in with Google"}
        </button>

        {/* Divider */}
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          marginBottom: 18,
        }}>
          <div style={{ flex: 1, height: 1, background: colors.border2 }} />
          <span style={{
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 9,
            color: colors.text3,
            letterSpacing: 1.5,
            textTransform: "uppercase",
          }}>
            or email
          </span>
          <div style={{ flex: 1, height: 1, background: colors.border2 }} />
        </div>

        {/* Form */}
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <label style={{
              display: "block",
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 10,
              fontWeight: 500,
              color: colors.text3,
              letterSpacing: 0.8,
              textTransform: "uppercase",
              marginBottom: 6,
            }}>
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              style={{
                width: "100%",
                padding: "10px 14px",
                borderRadius: 8,
                border: `0.5px solid ${colors.border2}`,
                background: colors.bg0,
                color: colors.text0,
                fontSize: 13,
                fontFamily: "'Inter', sans-serif",
                outline: "none",
                transition: "border-color 0.15s",
              }}
              onFocus={(e) => e.target.style.borderColor = colors.accent}
              onBlur={(e) => e.target.style.borderColor = colors.border2}
              onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
            />
          </div>

          <div>
            <label style={{
              display: "block",
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 10,
              fontWeight: 500,
              color: colors.text3,
              letterSpacing: 0.8,
              textTransform: "uppercase",
              marginBottom: 6,
            }}>
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              style={{
                width: "100%",
                padding: "10px 14px",
                borderRadius: 8,
                border: `0.5px solid ${colors.border2}`,
                background: colors.bg0,
                color: colors.text0,
                fontSize: 13,
                fontFamily: "'Inter', sans-serif",
                outline: "none",
                transition: "border-color 0.15s",
              }}
              onFocus={(e) => e.target.style.borderColor = colors.accent}
              onBlur={(e) => e.target.style.borderColor = colors.border2}
              onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
            />
          </div>

          {error && (
            <div style={{
              padding: "8px 12px",
              borderRadius: 6,
              background: colors.dangerSoft,
              color: colors.danger,
              fontSize: 12,
            }}>
              {error}
            </div>
          )}

          <button
            onClick={handleSubmit}
            disabled={loading}
            style={{
              width: "100%",
              padding: "12px 0",
              borderRadius: 8,
              border: "none",
              cursor: loading ? "not-allowed" : "pointer",
              fontSize: 14,
              fontWeight: 600,
              fontFamily: "'Inter', sans-serif",
              color: "#fff",
              background: loading ? colors.text3 : colors.accent,
              transition: "all 0.15s",
              marginTop: 4,
            }}
          >
            {loading ? "Authenticating..." : tab === "signin" ? "Sign in" : "Create account"}
          </button>
        </div>
      </div>

      {/* Theme toggle */}
      <button
        onClick={toggle}
        style={{
          marginTop: 20,
          padding: "6px 14px",
          borderRadius: 6,
          border: `0.5px solid ${colors.border2}`,
          background: "transparent",
          color: colors.text3,
          fontSize: 11,
          cursor: "pointer",
          fontFamily: "'JetBrains Mono', monospace",
          letterSpacing: 0.5,
        }}
      >
        {theme === "light" ? "◐ Dark mode" : "◑ Light mode"}
      </button>
    </div>
  );
}