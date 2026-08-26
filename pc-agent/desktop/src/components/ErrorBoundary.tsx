import { Component, ErrorInfo, ReactNode } from "react";

// Error boundary at the App root. Without this, any uncaught render error
// in a sub-component (a useEffect throws, a malformed sensor payload, etc.)
// white-screens the whole app and the user has no recourse short of force-
// quitting Tauri. The boundary catches the error, shows a fallback, and
// gives the user a button to retry — most errors clear after a remount.
//
// Errors are written to the browser console and (when wired up) to Sentry.
// We intentionally don't try to recover automatically: if a render keeps
// throwing on every mount, retrying would loop forever.

type Props = { children: ReactNode };
type State = { error: Error | null; info: ErrorInfo | null };

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error, info);
    this.setState({ info });
  }

  reset = () => this.setState({ error: null, info: null });

  render() {
    if (!this.state.error) return this.props.children;

    // Inline styles only — we can't trust ThemeContext to be intact at the
    // moment of a render error. Match the dark theme palette manually.
    return (
      <div style={{
        position: "fixed", inset: 0,
        background: "#07090e",
        color: "#e6e7ea",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 16,
        padding: 32,
        fontFamily: "'Inter', system-ui, sans-serif",
        textAlign: "center",
      }}>
        <div style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 11,
          letterSpacing: 2,
          textTransform: "uppercase",
          color: "#ff6b6b",
        }}>
          Something went wrong
        </div>
        <div style={{
          fontFamily: "'Space Grotesk', sans-serif",
          fontSize: 22,
          fontWeight: 600,
          maxWidth: 520,
        }}>
          The desktop UI hit an unexpected error and stopped rendering.
        </div>
        <div style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 11,
          color: "#9ba1a8",
          maxWidth: 720,
          padding: "12px 14px",
          background: "#10131a",
          border: "0.5px solid #1f242c",
          borderRadius: 8,
          textAlign: "left",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          maxHeight: 240,
          overflowY: "auto",
        }}>
          {this.state.error.message}
          {this.state.info?.componentStack ? "\n" + this.state.info.componentStack : ""}
        </div>
        <div style={{ display: "flex", gap: 12, marginTop: 8 }}>
          <button
            onClick={this.reset}
            style={{
              padding: "10px 20px",
              borderRadius: 8,
              border: "0.5px solid #4d8af0",
              background: "#1a3060",
              color: "#9bbdff",
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 12,
              fontWeight: 500,
              letterSpacing: 0.5,
              textTransform: "uppercase",
              cursor: "pointer",
            }}
          >
            Try again
          </button>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: "10px 20px",
              borderRadius: 8,
              border: "0.5px solid #2c333d",
              background: "transparent",
              color: "#e6e7ea",
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 12,
              fontWeight: 500,
              letterSpacing: 0.5,
              textTransform: "uppercase",
              cursor: "pointer",
            }}
          >
            Reload app
          </button>
        </div>
      </div>
    );
  }
}
