# Desktop thermal-model test — instructions for the test machine

Hi! You're helping validate a thermal-control thesis project by running
one experiment on your desktop and sending three files back.

The experiment takes about 10 minutes wall-clock once everything is
installed. It locks your CPU fan at a fixed PWM, applies a controlled
CPU load, records temperature for 4 minutes, removes the load, records
cooldown for 4 more minutes, then fits a first-order thermal model.

It does NOT change anything permanently. The fan returns to BIOS
control at the end (and on Ctrl+C too).

---

## Step 1 — What you need

- Windows 10 or 11
- Python 3.9 or newer
- Git
- 10 minutes during which the PC is otherwise idle
- A Powershell window opened **as Administrator** (right-click on
  PowerShell icon → "Run as Administrator"). LibreHardwareMonitor
  requires admin to read sensors and write fan PWM.

### Installing Python (skip if you already have it)

If `python --version` in Powershell tells you to "install from Microsoft
Store" — that's a fake alias, ignore it. Install the real Python:

1. Go to https://www.python.org/downloads/
2. Download the latest Python 3.x installer for Windows
3. **IMPORTANT during install: tick "Add python.exe to PATH"** at the
   bottom of the first screen
4. Click "Install Now"
5. After install, close all Powershell windows and open a new one
6. Verify: `python --version` should now print "Python 3.x.x"

## Step 2 — BIOS one-time setup (ASUS PRIME Z790-P)

Some BIOSes silently override software PWM writes with their own fan
curve. For ASUS PRIME Z790-P:

1. Reboot, press **Del** at boot to enter BIOS
2. Switch to **Advanced Mode** (F7 if you're in EZ Mode)
3. Go to **Monitor** tab → **Q-Fan Configuration**
4. Set **CPU Q-Fan Control** to **PWM** (not DC, not Auto)
5. Set **CPU Fan Profile** to **Manual**
6. If you see **AI Cooling II** anywhere — turn it off
7. Press F10, Save and Exit, reboot

The preflight script in Step 4 will detect if you skipped this and tell
you exactly what's wrong.

## Step 3 — Install

```powershell
git clone <repo-url> fan-control-system
cd fan-control-system\pc-agent

# Optional but recommended: isolated environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# Minimal deps (just enough for this experiment, ~40MB)
pip install -r requirements-experiment.txt
```

If `Activate.ps1` is blocked by execution policy, run it once:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

## Step 4 — Preflight check (30 seconds)

Open **Powershell as Administrator**, navigate to
`fan-control-system\pc-agent`, then:

```powershell
python Backend\scripts\desktop_preflight.py
```

This verifies, one by one:
- Python version
- Admin privileges
- Required Python packages installed
- LibreHardwareMonitor loads
- Your motherboard is detected as a desktop with PWM control
- CPU temperature is readable
- At least one fan is present
- **Software PWM writes actually change fan RPM** (the critical test:
  proves BIOS isn't ignoring our commands)

Every line is a `[PASS]` or `[FAIL]`. If you get
**"RESULT: ALL CHECKS PASSED"**, go to Step 5.

If anything `[FAIL]`s, the script tells you what to fix. Fix that thing
and re-run preflight. Don't skip to Step 5 until preflight is green.

## Step 5 — Run the experiment

Same admin Powershell:

```powershell
python Backend\scripts\desktop_step_response.py
```

The script will print what it's doing the entire time. Expect output
like:

```
[init] controller_name = lhm-pwm
[init] has_pwm_control = True
[detect] 3 fan(s) found
[detect] picking fan0 as CPU fan (highest idle RPM)
[lock] OK: fan0 stable at 1250 RPM
[soak] 60s idle baseline...
[heat] applying 4 stress procs, logging 240s...
  t=   30s  cpu=68C  fan_rpm=1248
  t=   60s  cpu=72C  fan_rpm=1250
  ...
[cool] logging 240s cooldown (fan still at PWM 50%)...
[done] CSV: ...
[fit] Heating: ...
[fit] Cooldown: ...
[fan-check] OK: fan stayed within 10% - clean experiment.
[xval]   verdict = GOOD: heating and cooldown agree within 20%
```

Total runtime: ~9 minutes (60s soak + 240s heat + 240s cool + a few
seconds for fan lock and fitting).

## Step 6 — What to send back

After completion, three files are written under
`pc-agent\Backend\scripts\`:

```
data\desktop_step_<timestamp>.csv       # raw 1Hz measurements
figures\desktop_step_<timestamp>.png    # heating + cooldown plot with fits
figures\desktop_step_<timestamp>.json   # K, tau, R^2 summary
```

Also: copy the **full Powershell output** of both the preflight and the
main script (Ctrl+A in Powershell, Ctrl+C to copy). Paste it into a
text file `run_log.txt` so I can see what happened end-to-end.

ZIP all four files and send back.

---

## Troubleshooting common issues

**Preflight: "Running as Administrator: FAIL"**
You opened a normal Powershell. Close it, right-click the Powershell
icon → "Run as Administrator", and re-run from there.

**Preflight: "Controller detected: 'sensors-only'"**
LHM didn't find the Super-I/O chip. Make sure you're admin, then check
that LibreHardwareMonitor.exe can read your fans (run
`Backend\lib\LibreHardwareMonitor.exe` and look for fan readings under
"Motherboard"). If LHM.exe doesn't see fans either, your chipset isn't
supported — let me know your motherboard model.

**Preflight: "Software PWM write changes fan RPM: FAIL"**
BIOS is overriding software writes. Go back to Step 2 (BIOS setup).
Specifically, the CPU Fan must be in **PWM Manual** mode, not Auto.

**Main script: "Heating CPU temperature reached TjMax"**
4 stress procs is too much for your CPU at 50% fan. Re-run with less:

```powershell
python Backend\scripts\desktop_step_response.py --stress-procs 2
```

**Main script: "Heating only reached 60C"**
Opposite — too little load. Increase or lower the fan:

```powershell
python Backend\scripts\desktop_step_response.py --stress-procs 8 --pwm-lock 35
```

Sweet spot for clean data: heating steady-state should land between
**75 and 88 C**.

**Main script: post-run "fan RPM varied >10%"**
BIOS intervened mid-experiment. Re-do BIOS setup (Step 2) more
aggressively — turn off any "smart cooling" or "auto" mode entirely,
even if it's not under the obvious fan control section.

---

## What the result means

If R² > 0.95 on both heating and cooldown, and the two τ values agree
within 20%, the experiment is a clean success. The thesis story
becomes: "the first-order LTI model is validated experimentally on a
desktop platform with continuous PWM control."

If something disagrees, that's still useful — the JSON tells us what's
off, and we can re-run with adjusted parameters.

Thanks!
