# ThermalControl Mobile

Mobile companion app for ThermalControl PC agent. Monitor CPU/GPU temperatures, switch fan profiles, and receive thermal alerts — all from your phone.

## Setup

### Prerequisites
- Node.js 18+ installed
- Expo Go app on your iPhone (from App Store)
- ThermalControl desktop agent running on your PC

### Install & Run

```bash
cd thermalcontrol-mobile
npm install
npx expo start
```

Scan the QR code with your iPhone camera (or Expo Go app).

### Project Structure

```
app/
  _layout.tsx          — Root layout, auth redirect
  index.tsx            — Entry redirect
  (auth)/
    _layout.tsx        — Auth stack
    login.tsx          — Login screen (Google OAuth + email)
  (tabs)/
    _layout.tsx        — Tab navigator (4 tabs)
    index.tsx          — Dashboard (live sensors, gauges)
    profiles.tsx       — Fan profiles (switch remotely)
    alerts.tsx         — Thermal alert history
    settings.tsx       — Device info, account, QR pair
components/
  Gauge.tsx            — Animated SVG circular gauge
  StatCard.tsx         — Metric card with progress bar
  CoreBars.tsx         — Per-core usage bar chart
hooks/
  useRealtimeSensors.ts — Supabase Realtime subscription
  useDevices.ts         — Device list management
lib/
  supabase.ts          — Supabase client config
  auth-context.tsx     — Auth state provider
  theme.ts             — Design tokens (matches desktop)
```

### Architecture

```
Mobile App  ←→  Supabase Cloud  ←→  PC Agent
  (read)    sensor_readings         (write)
  (write)   commands                (poll & execute)
  (read)    profiles                (sync)
  (read)    alerts                  (trigger)
```

The mobile app never connects directly to the PC. All communication goes through Supabase, so you can monitor from anywhere.

### Roadmap

- [x] Phase 1: Project setup, auth, navigation, Supabase
- [ ] Phase 2: Live dashboard with gauges and charts
- [ ] Phase 3: Profile switching, alerts, push notifications
- [ ] Phase 4: QR pairing, temp history, home widget, haptics
