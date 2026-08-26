# ThermalControl

Fan and thermal monitoring for Windows laptops. PC agent reads sensors via
LibreHardwareMonitor and exposes a local API; the desktop UI runs in the
same process as the agent (via Tauri sidecar) and shows live telemetry plus
fan controls; the mobile app pairs with the PC over Supabase and lets you
check temps / change profiles from your phone.

```
┌────────────┐   3.   ┌───────────────────────────────────────────────┐
│  Mobile    │──────▶ │  Supabase (Postgres + RLS + Realtime)         │
│  (Expo)    │ commands │                                             │
└────────────┘ profiles │  devices · sensor_readings · profiles ·     │
       ▲       alerts   │  commands · alerts · alert_settings ·       │
       │  realtime      │  pairings · command_audit · command_rate    │
       └────────────────│                                             │
                        └───────┬───────────────────────────────────┬─┘
                          1.    │ session JWT                       │ 4.
                                │ realtime / heartbeat / sensors    │ realtime
                       ┌────────▼─────────┐                ┌────────▼─────┐
                       │ pc-agent/Backend │                │ pc-agent/    │
                       │ Python agent     │ 2. /api/* + WS │ desktop      │
                       │ (FastAPI + LHM)  │◀──────────────▶│ Tauri+React  │
                       └──────────────────┘  127.0.0.1:8420└──────────────┘
                                ▲                                  ▲
                                └─── 5. spawn (sidecar) ───────────┘
```

## Components

| Folder                      | Stack                       | Lifetime               |
|-----------------------------|-----------------------------|------------------------|
| `pc-agent/Backend/`         | Python 3 + FastAPI          | Always-on, on the PC   |
| `pc-agent/desktop/`         | Tauri 2 + React 19 + TypeScript | Launched by user, spawns the agent |
| `mobile-app/`               | Expo Router 4 + React Native 0.76 | On user's phone   |
| `supabase/migrations/`      | SQL                         | Schema for the cloud DB |

The agent owns the hardware. Both the desktop and the mobile app read
Supabase to see live state, and write commands into a `commands` table that
the agent picks up via Realtime (with a 30s polling fallback). The desktop
additionally talks to the agent directly over `127.0.0.1:8420` for the
~2 second WebSocket sensor stream and synchronous fan controls.

## Auth model

- **Mobile** — Google OAuth via Supabase. Session lives in `expo-secure-store`.
- **Desktop** — Google OAuth via `tauri-plugin-oauth`. Session lives in localStorage. On sign-in the desktop's React app hands the access/refresh tokens to its Rust shell (`invoke('start_agent_with_session', ...)`), which spawns the bundled agent with `TAURI_ACCESS_TOKEN`/`TAURI_REFRESH_TOKEN` env vars set. **The agent is bound to whichever Supabase user signed in via the desktop UI.**
- **Agent (standalone)** — for users running `python Backend/agent.py` from a terminal, the first run prompts for a 6-character PIN that the mobile app generates. Credentials persist in the OS keyring after that.

The Supabase project URL and anon key are public-by-design and hardcoded in the clients. RLS scopes every read/write to the signed-in user.

## Hardware support

- **Sensors**: any laptop or desktop where LibreHardwareMonitor reads CPU/GPU temps. Most modern systems (Intel/AMD CPUs, NVIDIA/AMD/Intel GPUs).
- **Fan speed (PWM)**: motherboards with ITE/Nuvoton/Fintek chips that LHM can drive directly. Most desktop boards, some laptops.
- **Fan mode (BIOS thermal policy)**: Lenovo Legion only (Quiet/Balanced/Performance via WMI). Other vendors not yet supported.

The agent advertises its driver via the `controller` field on `/api/status` and the `devices.controller` column. Possible values: `lenovo-legion-wmi`, `lhm-pwm`, `sensors-only`, `demo`.

## Running it (dev mode)

```bash
# 1. Python agent
cd pc-agent
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
python Backend/agent.py
# First run: enter the PIN shown on mobile under "Add this PC"

# 2. Tauri desktop (separate terminal)
cd pc-agent/desktop
npm install
npm run tauri dev

# 3. Mobile (separate terminal — install Expo Go on your phone first)
cd mobile-app
npm install
npx expo start
# Scan the QR with Expo Go on Android, or the Camera app on iOS
```

## Building for distribution

The end goal is a single `.msi` for Windows + a single signed `.apk` for Android. See `MANUAL_FOLLOWUPS.md` §5 for the actual commands. Highlights:

- **Desktop**: `pc-agent/build_agent.bat` produces `pc-agent/dist/agent/agent.exe` (PyInstaller). Then `cd pc-agent/desktop && npm run tauri build` produces `pc-agent/desktop/src-tauri/target/release/bundle/msi/*.msi`. The MSI bundles the agent inside; the user double-clicks once and never opens a terminal.
- **Mobile**: `cd mobile-app && eas build --platform android --profile preview` produces a signed APK on EAS servers. Distribute via GitHub Releases or direct download.

## Layout

```
fan-control-system/
├── pc-agent/                       # Everything that runs on the user's PC
│   ├── Backend/                    # Python agent
│   │   ├── agent.py                # Entry point, monitoring loop, command dispatch
│   │   ├── cloud.py                # Supabase client wrapper
│   │   ├── hardware.py             # LHM + WMI sensor reads, fan control
│   │   ├── local_server.py         # FastAPI (REST + WebSocket) for the desktop
│   │   ├── pairing.py              # PIN flow + OS-keyring credential storage
│   │   ├── profiles.py             # Fan-curve interpolation engine
│   │   ├── realtime_listener.py    # Supabase realtime subscriber for commands
│   │   ├── wmi_fan.py              # Lenovo Legion-specific fan-mode WMI controller
│   │   └── lib/                    # LibreHardwareMonitor DLLs (vendor-neutral)
│   ├── desktop/                    # Tauri 2 + React 19 desktop UI
│   │   ├── src/                    # React (TS)
│   │   ├── src-tauri/              # Rust shell + sidecar lifecycle
│   │   └── package.json
│   ├── agent.spec                  # PyInstaller config for the bundled agent.exe
│   └── build_agent.bat             # One-line build trigger
├── mobile-app/                     # Expo Router + React Native
│   ├── app/                        # Expo Router file-based routes
│   ├── components/, hooks/, lib/
│   ├── eas.json                    # Production build profiles
│   └── app.json
├── supabase/migrations/            # SQL schema + RLS policies (apply in numerical order)
├── README.md                       # ← this file
└── MANUAL_FOLLOWUPS.md             # What you need to do manually (migrations, key gen, etc.)
```

## Where things live (jump table)

| Looking for…                              | Path                                                            |
|-------------------------------------------|-----------------------------------------------------------------|
| The fan-curve math                        | `pc-agent/Backend/profiles.py`                                  |
| The Lenovo Legion fan-mode WMI calls      | `pc-agent/Backend/wmi_fan.py`                                   |
| The realtime listener (sub-second commands) | `pc-agent/Backend/realtime_listener.py`                       |
| The local REST API (desktop ↔ agent)      | `pc-agent/Backend/local_server.py`                              |
| The Tauri sidecar that auto-launches agent | `pc-agent/desktop/src-tauri/src/sidecar.rs`                    |
| The desktop's auto-pair handshake          | `pc-agent/desktop/src/lib/AuthContext.tsx`                     |
| The mobile pairing UI (PIN generator)     | `mobile-app/app/pair.tsx`                                       |
| Where commands get validated server-side  | `supabase/migrations/0002_command_validator.sql`                |
| RLS policies                              | `supabase/migrations/0001_baseline_rls.sql`                     |
