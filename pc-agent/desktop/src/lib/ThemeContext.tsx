import { createContext, useContext, useState, useEffect, ReactNode } from "react";
import { light, dark, Colors, Theme } from "./theme";

// "system" follows the OS's prefers-color-scheme. "light" / "dark" are
// explicit overrides that survive across launches via localStorage.
//
// Resolution order on every mount:
//   1. localStorage["tc-theme"] — user's explicit choice if set
//   2. "system" — derive from window.matchMedia
//
// The toggle button cycles: light → dark → system → light → … so users
// who haven't chosen anything explicit start in "system" and can opt
// into a fixed mode. Existing users (with "light" or "dark" stored) keep
// their preference.
type ThemePref = Theme | "system";

type ThemeCtx = {
  theme: Theme;          // resolved (always "light" or "dark" — what the UI renders)
  pref: ThemePref;       // user's expressed preference (may be "system")
  colors: Colors;
  toggle: () => void;    // cycles light → dark → system → light
};

const ThemeContext = createContext<ThemeCtx>({
  theme: "light",
  pref: "system",
  colors: light,
  toggle: () => {},
});

const STORAGE_KEY = "tc-theme";
const VALID_PREFS: ThemePref[] = ["light", "dark", "system"];

function readStoredPref(): ThemePref {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw && (VALID_PREFS as string[]).includes(raw)) {
      return raw as ThemePref;
    }
  } catch {}
  // No stored value yet → default to system. Existing installs that had
  // "light" or "dark" stored above still hit the first branch.
  return "system";
}

function systemTheme(): Theme {
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [pref, setPref] = useState<ThemePref>(readStoredPref);
  const [systemResolved, setSystemResolved] = useState<Theme>(systemTheme);

  // Watch for OS-level theme changes while the app is running.
  useEffect(() => {
    let mq: MediaQueryList;
    try {
      mq = window.matchMedia("(prefers-color-scheme: dark)");
    } catch {
      return;
    }
    const onChange = (e: MediaQueryListEvent) => {
      setSystemResolved(e.matches ? "dark" : "light");
    };
    // Older Safari uses addListener; newer browsers use addEventListener.
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if ((mq as any).addListener) (mq as any).addListener(onChange);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener("change", onChange);
      else if ((mq as any).removeListener) (mq as any).removeListener(onChange);
    };
  }, []);

  const theme: Theme = pref === "system" ? systemResolved : pref;
  const colors = theme === "light" ? light : dark;

  const toggle = () =>
    setPref((current) => {
      // light → dark → system → light → …
      const next: ThemePref =
        current === "light" ? "dark" :
        current === "dark"  ? "system" :
                              "light";
      try { localStorage.setItem(STORAGE_KEY, next); } catch {}
      return next;
    });

  // Set data-theme on html for potential global CSS usage
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  return (
    <ThemeContext.Provider value={{ theme, pref, colors, toggle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
