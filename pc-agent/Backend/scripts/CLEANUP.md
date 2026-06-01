# Cleanup checklist — revert all changes after the experiment

After we finish collecting data, run through this list to put your
machine back to exactly the state it was in before you helped out.
Nothing here is destructive — it's purely undoing the temporary
modifications. Order doesn't matter, but follow top-to-bottom for
clarity.

## 1. Re-enable CPU Turbo Boost

You disabled Turbo via `PROCTHROTTLEMAX 99` so the experiment wouldn't
spike to TjMax. To restore default Turbo behaviour:

```powershell
# Run in PowerShell (admin not required, but admin works too)
powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX 100
powercfg /setactive SCHEME_CURRENT
```

Verify with Task Manager → Performance → CPU: under load the clock
should now go above 3.5 GHz (your i5-13600KF's base) — typical boost
hits 4.9-5.1 GHz on P-cores.

## 2. Revert BIOS Q-Fan setting (only if you want default behaviour back)

You set CPU Q-Fan Control to **PWM Manual** so software writes weren't
overridden. To restore the original ASUS automatic curve:

1. Reboot, press **Del** at boot
2. **Advanced Mode** (F7) → **Monitor** → **Q-Fan Configuration**
3. **CPU Q-Fan Control** → back to **Auto** (or **PWM** if you prefer
   PWM control but with an automatic curve, which is what most people
   keep)
4. **CPU Fan Profile** → back to **Standard** (or whatever you had —
   Silent/Performance are the other common options)
5. F10, Save and Exit

This step is OPTIONAL — Manual mode just means your fan stays at
whatever PWM was last written. Once you reboot, BIOS will read its
Profile default. So leaving it on Manual but rebooting effectively
restores the BIOS curve too.

## 3. Uninstall the WinRing0 kernel driver (optional)

LibreHardwareMonitor.exe installed `WinRing0_1_2_0` as a Windows
kernel service when you ran it. The driver is harmless — it's used by
every hardware monitor app (HWiNFO, Core Temp, MSI Afterburner, etc.)
to read CPU MSRs. If you want a fully clean uninstall:

```powershell
# Run in admin PowerShell
sc.exe stop WinRing0_1_2_0
sc.exe delete WinRing0_1_2_0
```

If you ever want hardware monitoring tools again, they'll re-install
the driver automatically. Most people just leave it.

## 4. Delete the cloned repository

```powershell
Remove-Item -Recurse -Force C:\Users\gherm\Desktop\Marius_licenta
```

This removes:
- The git clone
- All Python packages installed in any local venv inside the repo
- All CSVs/PNGs/JSONs generated during experiments
- LibreHardwareMonitor's runtime config file

## 5. Uninstall Python (optional)

You installed Python 3.11.9 from python.org. If you don't use it for
anything else and want it gone:

1. **Settings → Apps → Installed apps**
2. Find **Python 3.11.9 (64-bit)** → **...** → **Uninstall**
3. Also remove **Python Launcher** if present

If you used `pip install -r requirements-experiment.txt` directly
(not in a venv), the packages (numpy, scipy, pandas, matplotlib,
pythonnet, psutil) sit in your user site-packages and get removed
along with Python.

## 6. (No action needed) Memory Integrity

You said Memory Integrity was already OFF before any of this, so
nothing to revert there. If you want to re-enable it for hardening:

1. **Settings → Privacy and Security → Windows Security → Device
   Security → Core Isolation Details**
2. **Memory Integrity → ON**
3. Reboot

---

## Summary of what was changed temporarily

| Item | Default | Set to | How to revert |
|------|---------|--------|---------------|
| CPU Turbo Boost (Windows pwr scheme) | enabled | disabled (PROCTHROTTLEMAX 99) | Step 1 |
| BIOS CPU Q-Fan mode | Auto / Standard | PWM / Manual | Step 2 |
| WinRing0 driver | not installed | installed by LHM.exe | Step 3 (optional) |
| Repository on Desktop | n/a | cloned | Step 4 |
| Python install | not present | 3.11.9 installed | Step 5 (optional) |

Thanks for helping out!
