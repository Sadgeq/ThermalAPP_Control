import { createContext, useContext, useState, useEffect, ReactNode } from "react";
import { light, dark, Colors, Theme } from "./theme";

type ThemeCtx = {
  theme: Theme;
  colors: Colors;
  toggle: () => void;
};

const ThemeContext = createContext<ThemeCtx>({
  theme: "light",
  colors: light,
  toggle: () => {},
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("tc-theme") as Theme) || "light";
    } catch {
      return "light";
    }
  });

  const toggle = () =>
    setTheme((t) => {
      const next = t === "light" ? "dark" : "light";
      try { localStorage.setItem("tc-theme", next); } catch {}
      return next;
    });

  const colors = theme === "light" ? light : dark;

  // Set data-theme on html for potential global CSS usage
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  return (
    <ThemeContext.Provider value={{ theme, colors, toggle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}