import { useTheme } from "../lib/ThemeContext";
import type { Page } from "../App";

type Props = {
  active: Page;
  onNavigate: (page: Page) => void;
};

const ICONS: { id: Page; svg: string }[] = [
  {
    id: "dashboard",
    svg: `<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>`,
  },
  {
    id: "profiles",
    svg: `<path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z"/>`,
  },
  {
    id: "logs",
    svg: `<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>`,
  },
  {
    id: "settings",
    svg: `<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/>`,
  },
];

export default function IconSidebar({ active, onNavigate }: Props) {
  const { colors } = useTheme();

  return (
    <div style={{
      width: 52,
      background: colors.sidebar,
      borderRight: `0.5px solid ${colors.border}`,
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      padding: "14px 0",
      gap: 6,
      flexShrink: 0,
    }}>
      {ICONS.map((item) => {
        const isActive = active === item.id;
        return (
          <button
            key={item.id}
            onClick={() => onNavigate(item.id)}
            title={item.id.charAt(0).toUpperCase() + item.id.slice(1)}
            style={{
              width: 36,
              height: 36,
              borderRadius: 8,
              border: "none",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: isActive ? colors.accentSoft : "transparent",
              color: isActive ? colors.accent : colors.text3,
              transition: "all 0.15s ease",
            }}
          >
            <svg
              width="18" height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              dangerouslySetInnerHTML={{ __html: item.svg }}
            />
          </button>
        );
      })}
    </div>
  );
}