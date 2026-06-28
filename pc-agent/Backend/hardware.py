"""
Hardware Monitor
================
Reads CPU/GPU temps, fan RPM, controls fan speed via LibreHardwareMonitor.

IMPORTANT: Extract the FULL LibreHardwareMonitor zip into ./lib/
Not just the main DLL - it needs System.Memory.dll and other deps.

SECURITY FIXES:
  - AssemblyResolve only loads DLLs from the verified lib directory (whitelist)
  - DLL path validated with realpath to prevent symlink attacks
  - fan_index bounds checking with negative index prevention
  - All psutil calls wrapped in try/except for robustness
  - set_fan_speed validates float conversion
"""

import logging
import os
import random
import sys
from pathlib import Path

import psutil

logger = logging.getLogger("Hardware")

# Resolve to absolute paths once at import time
_BASE_DIR = Path(__file__).parent.resolve()
LHM_DLL_PATHS = [
    _BASE_DIR / "lib" / "LibreHardwareMonitorLib.dll",
    _BASE_DIR / "LibreHardwareMonitorLib.dll",
]


def _try_load_lhm():
    """Load LHM .NET DLL with automatic dependency resolution."""
    if sys.platform != "win32":
        logger.info("Not on Windows, LHM unavailable")
        return False
    try:
        import clr
        import System
    except ImportError:
        logger.warning("pythonnet not installed")
        return False

    for dll_path in LHM_DLL_PATHS:
        if not dll_path.exists():
            continue

        # FIX: Resolve to real path to prevent symlink-based DLL injection
        resolved = dll_path.resolve()
        dll_dir = str(resolved.parent)

        # Verify the DLL is within our expected directory tree
        if not str(resolved).startswith(str(_BASE_DIR)):
            logger.error(f"SECURITY: DLL path escapes base directory: {resolved}")
            continue

        # Add DLL directory to PATH for .NET dependency resolution
        os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")

        # Register AssemblyResolve so .NET finds deps in the same folder
        try:
            import System.Reflection

            # FIX: Only load DLLs from the verified directory, validate name format
            def _resolve(sender, args):
                name = args.Name.split(",")[0]
                # Sanitize: only allow alphanumeric, dots, hyphens in DLL names
                if not all(c.isalnum() or c in ('.', '-', '_') for c in name):
                    logger.warning(f"SECURITY: Rejected suspicious assembly name: {name}")
                    return None
                dll_name = name + ".dll"
                candidate = os.path.join(dll_dir, dll_name)
                # Verify resolved path stays within dll_dir (prevent path traversal)
                real_candidate = os.path.realpath(candidate)
                if not real_candidate.startswith(dll_dir):
                    logger.warning(f"SECURITY: Assembly path traversal blocked: {candidate}")
                    return None
                if os.path.isfile(real_candidate):
                    return System.Reflection.Assembly.LoadFrom(real_candidate)
                return None

            System.AppDomain.CurrentDomain.AssemblyResolve += _resolve
        except Exception as e:
            logger.warning(f"AssemblyResolve setup failed: {e}")

        try:
            clr.AddReference(str(resolved))
            logger.info(f"Loaded LHM from {resolved} (deps: {dll_dir})")
            return True
        except Exception as e:
            logger.error(f"Failed to load LHM: {e}")
            return False

    logger.warning("LibreHardwareMonitorLib.dll not found")
    return False


class HardwareMonitor:

    def __init__(self, force_demo=False):
        self._lhm_available = False if force_demo else _try_load_lhm()
        self._computer = None
        self._fans = []
        self._fan_controllers = []
        self.fan_count = 0
        self._demo_temps = {"cpu": 38.0, "gpu": 33.0}
        self._demo_fan_pct = {0: 35.0, 1: 30.0}

        # Lenovo Legion fan-mode controller (Quiet/Balanced/Performance).
        # This is what flips the Y-key LED color and changes the BIOS fan
        # policy — the LHM SetSoftware path can't do that. Initialized
        # lazily on first set_fan_mode call to avoid loading the wmi
        # package on non-Lenovo machines.
        self._wmi_fan = None
        # One-time guard so the "fell back to WMI for fan reads" log line
        # in read_fan_speeds doesn't spam every monitoring tick.
        self._wmi_fans_logged = False
        # Same one-shot for "no LHM PWM controllers" — set_fan_speed is
        # called every tick by the monitoring loop, we don't want a
        # warning per call on hardware that lacks per-fan PWM.
        self._pwm_unavailable_logged = False

        # Vendor / model fingerprint, queried once at boot. Used to
        # advertise the chosen controller and to gate vendor-specific
        # paths (e.g. Legion fan-mode WMI).
        self.vendor, self.model = self._detect_system()

        if self._lhm_available:
            self._init_lhm()
            # On Lenovo Legion, LHM almost never enumerates fans as
            # SensorType.Fan (the EC doesn't expose them that way), so
            # fan_count stays 0 even though the WMI namespace
            # LENOVO_GAMEZONE_DATA can both read RPM and set fan mode.
            # Reflect WMI's view in fan_count so downstream code (the
            # agent's monitoring loop, PID dispatch, /api/status) treats
            # the machine as having fans. Without this the PID controller
            # is gated off behind `if fan_count > 0` and silently
            # contributes nothing.
            if self.fan_count == 0 and self.vendor.lower().startswith("lenovo"):
                try:
                    wmi_ctrl = self._ensure_wmi_fan()
                    if wmi_ctrl is not None:
                        wmi_count = wmi_ctrl.read_fan_count() or 2
                        wmi_count = max(1, min(int(wmi_count), 4))
                        self.fan_count = wmi_count
                        logger.info(
                            f"fan_count set to {wmi_count} via Legion WMI "
                            f"(LHM saw 0 fans)"
                        )
                except Exception as e:
                    logger.debug(f"WMI fan_count probe failed: {e}")
        else:
            self.fan_count = 2
            logger.info("Running in DEMO mode")

    @staticmethod
    def _detect_system() -> tuple[str, str]:
        """Read manufacturer + model from Win32_ComputerSystem once at boot.

        Cheap, blocking ~50ms PowerShell call. We do it once so vendor
        detection is available without a WMI roundtrip per check. Returns
        ('', '') on non-Windows or if the query fails — callers handle
        absence as 'unknown vendor'.
        """
        if sys.platform != "win32":
            return ("", "")
        try:
            import subprocess
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "$cs = Get-CimInstance Win32_ComputerSystem; "
                 "Write-Output \"$($cs.Manufacturer)|$($cs.Model)\""],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                line = (result.stdout or "").strip().splitlines()[-1] if result.stdout else ""
                if "|" in line:
                    vendor, model = line.split("|", 1)
                    return (vendor.strip()[:64], model.strip()[:128])
        except Exception as e:
            logger.debug(f"Vendor detection failed: {e}")
        return ("", "")

    @property
    def controller_name(self) -> str:
        """Human-readable identifier for the fan-control path in use.

        Stable string clients can show in 'Driver: <name>' fields and that
        we can grep for in support tickets. Doesn't depend on _wmi_fan being
        initialized — the lazy load happens on first set_fan_mode.
        """
        if not self._lhm_available:
            return "demo"
        if self.vendor.lower().startswith("lenovo"):
            return "lenovo-legion-wmi"
        if self._fan_controllers:
            return "lhm-pwm"
        return "sensors-only"

    @property
    def has_pwm_control(self) -> bool:
        """True if the hardware exposes per-fan continuous PWM control.

        On Lenovo Legion (and most consumer laptops) the EC only accepts a
        coarse 3-state FanMode (Quiet/Balanced/Performance) through WMI,
        not a continuous duty-cycle. set_fan_speed() then becomes a no-op,
        and PID output has to be quantized to FanMode in agent.py.
        """
        return bool(self._fan_controllers)

    @property
    def capabilities(self) -> dict:
        """What this hardware actually lets us do.

        Surfaced via /api/status so the UI can show 'Sensor monitoring only'
        instead of dead fan-control buttons on unsupported hardware.
        """
        return {
            "sensors":  bool(self._lhm_available) or True,  # demo also reads sensors
            "fan_speed": bool(self._fan_controllers),
            "fan_mode":  self.vendor.lower().startswith("lenovo") and self._lhm_available,
            "demo":     not self._lhm_available,
        }

    def _init_lhm(self):
        from LibreHardwareMonitor.Hardware import Computer
        self._computer = Computer()
        self._computer.IsCpuEnabled = True
        self._computer.IsGpuEnabled = True
        self._computer.IsMotherboardEnabled = True
        self._computer.IsMemoryEnabled = True
        self._computer.IsControllerEnabled = True
        # Storage enumeration is intentionally OFF.
        # LHM's Storage.UpdateSpaceSensors() crashes with
        # IndexOutOfRangeException on some Windows volume layouts
        # (e.g., recovery partitions without a drive letter, BitLocker-
        # locked volumes, or virtual drives created by some VPN/security
        # tools). The exception bubbles up out of `hw.Update()` inside
        # _lhm_read, which then falls back to demo data — the symptom is
        # cpu_temp permanently reading 38°C and the PID controller never
        # actually regulating anything. We don't surface storage temps in
        # the UI anyway, so disabling the whole Storage tree is safe.
        self._computer.IsStorageEnabled = False
        self._computer.Open()
        self._discover_hardware()
        # Log all detected sensors for diagnostics
        self._log_all_sensors()
        logger.info(f"LHM initialized: {self.fan_count} fan(s)")

    def _log_all_sensors(self):
        """Log every detected sensor — helps diagnose missing CPU temp."""
        from LibreHardwareMonitor.Hardware import SensorType
        type_names = {
            SensorType.Temperature: "Temp",
            SensorType.Load: "Load",
            SensorType.Fan: "Fan",
            SensorType.Control: "Ctrl",
            SensorType.Clock: "Clock",
            SensorType.Voltage: "Volt",
            SensorType.Power: "Power",
            SensorType.SmallData: "Data",
        }
        logger.info("=== LHM Sensor Dump ===")
        for hw in self._computer.Hardware:
            hw.Update()
            logger.info(f"  HW: {hw.Name} ({hw.HardwareType})")
            for s in hw.Sensors:
                tname = type_names.get(s.SensorType, str(s.SensorType))
                val = f"{float(s.Value):.1f}" if s.Value is not None else "null"
                logger.info(f"    [{tname}] {s.Name} = {val}")
            for sub in hw.SubHardware:
                sub.Update()
                logger.info(f"    Sub: {sub.Name} ({sub.HardwareType})")
                for s in sub.Sensors:
                    tname = type_names.get(s.SensorType, str(s.SensorType))
                    val = f"{float(s.Value):.1f}" if s.Value is not None else "null"
                    logger.info(f"      [{tname}] {s.Name} = {val}")
        logger.info("=== End Sensor Dump ===")

    def _discover_hardware(self):
        from LibreHardwareMonitor.Hardware import SensorType
        self._fans.clear()
        self._fan_controllers.clear()
        for hw in self._computer.Hardware:
            hw.Update()
            for sub in hw.SubHardware:
                sub.Update()
            self._collect_fan_sensors(hw.Sensors)
            for sub in hw.SubHardware:
                self._collect_fan_sensors(sub.Sensors)
        self.fan_count = len(self._fans)

    def _collect_fan_sensors(self, sensors):
        from LibreHardwareMonitor.Hardware import SensorType
        for s in sensors:
            if s.SensorType == SensorType.Fan:
                self._fans.append(s)
            elif s.SensorType == SensorType.Control:
                self._fan_controllers.append(s)

    def read_sensors(self):
        if not self._lhm_available:
            return self._demo_data()
        try:
            return self._lhm_read()
        except Exception as e:
            logger.error(f"LHM read failed, falling back to demo: {e}")
            return self._demo_data()

    def read_fan_speeds(self):
        """Return [{name, rpm, percent}] for every fan we can read.

        Fan reading on Lenovo Legion (and probably other Legion EC variants)
        is unreliable through LibreHardwareMonitor — see wmi_fan.py header.
        Two failure modes happen in the wild:

          * LHM detects fan sensors but their .Value reads come back 0 or
            None because the EC registers LHM polls don't expose live RPM.
          * LHM doesn't detect fans at all because the EC doesn't surface
            them with SensorType.Fan; they only show up via Lenovo's WMI
            namespace (LENOVO_GAMEZONE_DATA.GetFan1Speed / GetFan2Speed).

        We handle both. If LHM detected fans, we try LHM first and fall
        back to WMI per-fan when LHM reports 0. If LHM detected no fans,
        we synthesize the list directly from WMI.
        """
        if not self._lhm_available:
            return self._demo_fans()
        try:
            for hw in self._computer.Hardware:
                # Same per-hardware isolation as _lhm_read — Storage and
                # other flaky components shouldn't take down fan reads.
                try:
                    hw.Update()
                    for sub in hw.SubHardware:
                        try:
                            sub.Update()
                        except Exception as e:
                            logger.debug(f"LHM SubHardware {sub.Name} Update failed: {e}")
                except Exception as e:
                    logger.debug(f"LHM Hardware {hw.Name} Update failed: {e}")
                    continue

            wmi_ctrl = self._ensure_wmi_fan()
            result: list[dict] = []

            if self._fans:
                # LHM saw fans. Use them as the primary source; fall back
                # to WMI for RPM where LHM reports 0.
                for i, fan in enumerate(self._fans):
                    rpm = int(fan.Value) if fan.Value is not None else 0
                    if rpm == 0 and wmi_ctrl is not None:
                        wmi_rpm = wmi_ctrl.read_fan_rpm(i)
                        if wmi_rpm and wmi_rpm > 0:
                            rpm = wmi_rpm
                    pct = None
                    if i < len(self._fan_controllers):
                        v = self._fan_controllers[i].Value
                        pct = float(v) if v is not None else None
                    result.append({"name": str(fan.Name), "rpm": rpm, "percent": pct})
                return result

            # LHM saw no fans. On Legion this is common — the EC doesn't
            # surface fans as SensorType.Fan. Build the list entirely from
            # WMI. Without a PWM controller value, compute percent as
            # rpm / max_rpm so the UI's "% of max" indicator means
            # something instead of always reading 0%.
            if wmi_ctrl is not None:
                count = wmi_ctrl.read_fan_count() or 2     # Legion has 2; safe default
                count = max(1, min(int(count), 4))         # bound it
                max_rpm = wmi_ctrl.read_max_rpm() or 0
                names = ["CPU Fan", "GPU Fan", "Fan 3", "Fan 4"]
                for i in range(count):
                    rpm = wmi_ctrl.read_fan_rpm(i) or 0
                    pct = (rpm / max_rpm * 100.0) if max_rpm > 0 else None
                    result.append({"name": names[i], "rpm": rpm, "percent": pct})
                if not self._wmi_fans_logged:
                    logger.info(
                        f"Fan reads via WMI fallback: {count} fan(s), max {max_rpm} rpm "
                        f"(LHM didn't detect fans on this hardware)"
                    )
                    self._wmi_fans_logged = True
                return result

            # No source of fan data on this hardware — sensors-only mode.
            return []
        except Exception as e:
            logger.error(f"Failed to read fan speeds: {e}")
            return self._demo_fans()

    def _lhm_read(self):
        from LibreHardwareMonitor.Hardware import SensorType, HardwareType
        cpu_temps = []
        cpu_load = None
        cpu_per_core = []
        cpu_name = None
        gpu_temp = None
        gpu_hot_spot = None
        gpu_clock_core = None
        gpu_clock_mem = None
        gpu_mem_used = None
        gpu_mem_total = None
        gpu_load = None
        gpu_name = None
        storage_temps = []

        for hw in self._computer.Hardware:
            # Per-hardware try: if one component (e.g. Storage, a flaky GPU)
            # throws on Update(), we skip just that component instead of
            # losing all sensor reads for the tick. CPU temp must still
            # reach the agent for PID/curve to act.
            try:
                hw.Update()
                for sub in hw.SubHardware:
                    try:
                        sub.Update()
                    except Exception as e:
                        logger.debug(f"LHM SubHardware {sub.Name} Update failed: {e}")
            except Exception as e:
                logger.debug(f"LHM Hardware {hw.Name} Update failed: {e}")
                continue

            # --- CPU ---
            if hw.HardwareType == HardwareType.Cpu:
                cpu_name = str(hw.Name)
                core_loads = {}
                for s in hw.Sensors:
                    if s.Value is None:
                        continue
                    if s.SensorType == SensorType.Temperature:
                        cpu_temps.append((s.Name, float(s.Value)))
                    elif s.SensorType == SensorType.Load:
                        if "Total" in s.Name:
                            cpu_load = float(s.Value)
                        elif "Core" in s.Name and "Thread" not in s.Name and "Max" not in s.Name:
                            core_loads[s.Name] = float(s.Value)
                        elif "Core #" in s.Name and "Thread #1" in s.Name:
                            core_name = s.Name.split("Thread")[0].strip()
                            if core_name not in core_loads:
                                core_loads[core_name] = float(s.Value)
                for name in sorted(core_loads.keys()):
                    cpu_per_core.append({"name": name, "load": round(core_loads[name], 1)})

            # --- GPU (NVIDIA, AMD, Intel) ---
            elif hw.HardwareType in (
                HardwareType.GpuNvidia, HardwareType.GpuAmd, HardwareType.GpuIntel,
            ):
                gpu_name = str(hw.Name)
                for s in hw.Sensors:
                    if s.Value is None:
                        continue
                    if s.SensorType == SensorType.Temperature:
                        name_lower = s.Name.lower()
                        if "hot spot" in name_lower or "hotspot" in name_lower:
                            gpu_hot_spot = float(s.Value)
                        elif gpu_temp is None:
                            gpu_temp = float(s.Value)
                    elif s.SensorType == SensorType.Clock:
                        if "Core" in s.Name and "Memory" not in s.Name:
                            gpu_clock_core = round(float(s.Value))
                        elif "Memory" in s.Name:
                            gpu_clock_mem = round(float(s.Value))
                    elif s.SensorType == SensorType.SmallData:
                        name_lower = s.Name.lower()
                        if "memory total" in name_lower:
                            gpu_mem_total = round(float(s.Value))
                        elif "memory used" in name_lower and "d3d" not in name_lower:
                            gpu_mem_used = round(float(s.Value))
                    elif s.SensorType == SensorType.Load:
                        if s.Name == "GPU Core" or s.Name == "GPU Load":
                            gpu_load = round(float(s.Value), 1)

            # --- Motherboard sub-hardware ---
            elif hw.HardwareType == HardwareType.Motherboard:
                for sub in hw.SubHardware:
                    for s in sub.Sensors:
                        if s.Value is None:
                            continue
                        if s.SensorType == SensorType.Temperature:
                            name_lower = s.Name.lower()
                            if "cpu" in name_lower or "cputin" in name_lower:
                                cpu_temps.append((s.Name, float(s.Value)))

            # --- Storage temps ---
            elif hw.HardwareType == HardwareType.Storage:
                for s in hw.Sensors:
                    if s.Value is None or s.SensorType != SensorType.Temperature:
                        continue
                    name_lower = s.Name.lower()
                    if "warning" in name_lower or "critical" in name_lower:
                        continue
                    storage_temps.append({
                        "drive": str(hw.Name),
                        "sensor": str(s.Name),
                        "temp": round(float(s.Value), 1),
                    })

        # Pick best CPU temp
        cpu_temp = None
        for keyword in ["package", "tctl", "tdie", "core (max)", "cpu"]:
            for name, val in cpu_temps:
                if keyword in name.lower():
                    cpu_temp = val
                    break
            if cpu_temp is not None:
                break
        if cpu_temp is None and cpu_temps:
            cpu_temp = cpu_temps[0][1]

        # FIX: If LHM found no CPU temp, try WMI/ACPI thermal zone as fallback
        if cpu_temp is None:
            logger.info("CPU temp not found in LHM, trying WMI fallback...")
            cpu_temp = self._wmi_cpu_temp()
            if cpu_temp is not None:
                logger.info(f"WMI fallback succeeded: CPU temp = {cpu_temp}C")
            else:
                logger.debug("WMI fallback returned None")

        if gpu_temp is None and gpu_hot_spot is not None:
            gpu_temp = gpu_hot_spot

        # FIX: All psutil calls wrapped in try/except for robustness
        disk_read = disk_write = 0.0
        try:
            disk = psutil.disk_io_counters()
            if disk:
                disk_read = round(disk.read_bytes / 1024 / 1024, 1)
                disk_write = round(disk.write_bytes / 1024 / 1024, 1)
        except Exception as e:
            logger.debug(f"psutil disk_io_counters failed: {e}")

        net_sent = net_recv = 0.0
        try:
            net = psutil.net_io_counters()
            if net:
                net_sent = round(net.bytes_sent / 1024 / 1024, 1)
                net_recv = round(net.bytes_recv / 1024 / 1024, 1)
        except Exception as e:
            logger.debug(f"psutil net_io_counters failed: {e}")

        # Per-core fallback via psutil if LHM didn't provide
        if not cpu_per_core:
            try:
                per_cpu = psutil.cpu_percent(percpu=True)
                cpu_per_core = [{"name": f"Core #{i+1}", "load": v} for i, v in enumerate(per_cpu)]
            except Exception as e:
                logger.debug(f"psutil cpu_percent(percpu) failed: {e}")

        ram_usage = 0.0
        try:
            ram_usage = psutil.virtual_memory().percent
        except Exception as e:
            logger.debug(f"psutil virtual_memory failed: {e}")

        cpu_load_fallback = 0.0
        if cpu_load is None:
            try:
                cpu_load_fallback = psutil.cpu_percent()
            except Exception:
                pass

        return {
            "cpu_temp": cpu_temp,
            "cpu_load": cpu_load if cpu_load is not None else cpu_load_fallback,
            "cpu_per_core": cpu_per_core,
            "cpu_name": cpu_name,
            "gpu_temp": gpu_temp,
            "gpu_hot_spot": gpu_hot_spot,
            "gpu_load": gpu_load,
            "gpu_name": gpu_name,
            "gpu_clock_core": gpu_clock_core,
            "gpu_clock_mem": gpu_clock_mem,
            "gpu_mem_used": gpu_mem_used,
            "gpu_mem_total": gpu_mem_total,
            "fan_speeds": self.read_fan_speeds(),
            "ram_usage": ram_usage,
            "storage_temps": storage_temps,
            "disk_io": {"read_mb": disk_read, "write_mb": disk_write},
            "network": {"sent_mb": net_sent, "recv_mb": net_recv},
        }

    def set_fan_speed(self, fan_index, speed_percent):
        """Set fan speed with full validation."""
        # FIX: Validate types before processing
        try:
            fan_index = int(fan_index)
            speed_percent = float(speed_percent)
        except (TypeError, ValueError) as e:
            logger.error(f"Invalid fan speed args: index={fan_index}, speed={speed_percent}: {e}")
            return

        speed_percent = max(0.0, min(100.0, speed_percent))

        # FIX: Prevent negative index access (Python allows a[-1])
        if fan_index < 0:
            logger.error(f"Negative fan_index rejected: {fan_index}")
            return

        if not self._lhm_available:
            if fan_index < self.fan_count:
                self._demo_fan_pct[fan_index] = speed_percent
            return

        if not self._fan_controllers:
            # No LHM PWM controllers detected — typical on Lenovo Legion,
            # which only exposes a 3-state FanMode through WMI (handled in
            # agent.py via the PWM->FanMode quantizer). Silently no-op
            # here instead of warning every tick. Logged once at startup.
            if not self._pwm_unavailable_logged:
                logger.info(
                    "Per-fan PWM control unavailable on this hardware; "
                    "PID output will be quantized to FanMode by agent."
                )
                self._pwm_unavailable_logged = True
            return
        if fan_index >= len(self._fan_controllers):
            logger.warning(f"Fan index {fan_index} out of range (max {len(self._fan_controllers) - 1})")
            return
        try:
            self._fan_controllers[fan_index].Control.SetSoftware(speed_percent)
        except Exception as e:
            logger.error(f"Failed to set fan {fan_index} to {speed_percent}%: {e}")

    # ------------------------------------------------------------------
    # Lenovo Legion fan-mode (1=Quiet, 2=Balanced, 3=Performance).
    # Flips the Y-key LED color and changes BIOS thermal policy.
    # ------------------------------------------------------------------
    def _ensure_wmi_fan(self):
        if self._wmi_fan is not None:
            return self._wmi_fan
        try:
            from wmi_fan import WmiFanController
        except Exception as e:
            logger.debug(f"wmi_fan not importable: {e}")
            return None
        ctrl = WmiFanController()
        if not ctrl.initialize():
            logger.info("WMI fan controller unavailable on this machine")
            self._wmi_fan = None
            return None
        self._wmi_fan = ctrl
        return ctrl

    def set_fan_mode(self, mode: int) -> bool:
        """Set Lenovo Legion fan mode (1/2/3). Returns True on success."""
        try:
            mode = int(mode)
        except (TypeError, ValueError):
            return False
        if mode not in (1, 2, 3):
            logger.warning(f"set_fan_mode: invalid mode {mode}")
            return False
        ctrl = self._ensure_wmi_fan()
        if ctrl is None:
            return False
        return ctrl.set_mode(mode)

    def get_fan_mode(self) -> int | None:
        """Return current Legion fan mode, or None if unavailable."""
        ctrl = self._ensure_wmi_fan()
        if ctrl is None:
            return None
        return ctrl.get_mode()

    def reset_fan_control(self, fan_index):
        """Reset fan to automatic/default control."""
        if not self._lhm_available:
            return
        if not isinstance(fan_index, int) or fan_index < 0:
            return
        if fan_index < len(self._fan_controllers):
            try:
                self._fan_controllers[fan_index].Control.SetDefault()
            except Exception as e:
                logger.error(f"Failed to reset fan {fan_index}: {e}")

    def get_fan_info(self):
        if not self._lhm_available:
            return [
                {"index": 0, "name": "CPU Fan", "has_controller": True},
                {"index": 1, "name": "Chassis Fan 1", "has_controller": True},
            ]
        return [
            {"index": i, "name": str(f.Name), "has_controller": i < len(self._fan_controllers)}
            for i, f in enumerate(self._fans)
        ]

    def close(self):
        """Graceful shutdown: reset all fans to default, then close."""
        if self._computer:
            for i in range(len(self._fan_controllers)):
                self.reset_fan_control(i)
            try:
                self._computer.Close()
            except Exception as e:
                logger.debug(f"Computer.Close() error: {e}")
            self._computer = None
            logger.info("Hardware monitor closed")

    # Each entry: (label, powershell command, parser function -> celsius or None)
    _WMI_CPU_TEMP_METHODS = [
        ("MSAcpi",
         "Get-CimInstance MSAcpi_ThermalZoneTemperature -Namespace root/wmi -ErrorAction Stop | Select -First 1 -ExpandProperty CurrentTemperature",
         lambda raw: (float(raw) / 10.0) - 273.15),
        ("OHM",
         "Get-CimInstance -Namespace root/OpenHardwareMonitor -ClassName Sensor -ErrorAction Stop | Where-Object {$_.SensorType -eq 'Temperature' -and $_.Name -like '*CPU*'} | Select -First 1 -ExpandProperty Value",
         lambda raw: float(raw)),
        ("LHM",
         "Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor -ErrorAction Stop | Where-Object {$_.SensorType -eq 'Temperature' -and $_.Name -like '*CPU*'} | Select -First 1 -ExpandProperty Value",
         lambda raw: float(raw)),
    ]

    def _wmi_cpu_temp(self):
        """Fallback: read CPU temp from Windows WMI when LHM doesn't have one.

        Each subprocess call to PowerShell costs 50-500ms when it works and
        up to the timeout (now 1.5s) when it doesn't. Trying all three
        methods on every sensor read is what stalled the worker thread for
        15s in the worst case before this rewrite. We now:
          1. Cache which method succeeded (`_wmi_cpu_method_idx`) so we
             only call the working one on subsequent reads.
          2. Set a `_wmi_cpu_dead` flag if all three fail, so a system
             with no working WMI temp source doesn't keep retrying.
          3. Use psutil as a last resort (mostly useful on Linux dev boxes).
        """
        # Permanent giveup — set when first probe of all three failed.
        if getattr(self, "_wmi_cpu_dead", False):
            return None

        if sys.platform != "win32":
            # Try psutil; useful when running the agent on Linux for dev.
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    for entries in temps.values():
                        for entry in entries:
                            if entry.current and 0 < entry.current < 150:
                                return round(entry.current, 1)
            except Exception:
                pass
            self._wmi_cpu_dead = True
            return None

        import subprocess

        cached_idx = getattr(self, "_wmi_cpu_method_idx", None)
        # If we've already found a working method, only try that one. If not,
        # try all of them in order until one works.
        indices = [cached_idx] if cached_idx is not None else range(len(self._WMI_CPU_TEMP_METHODS))

        for i in indices:
            label, cmd, parse = self._WMI_CPU_TEMP_METHODS[i]
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", cmd],
                    capture_output=True, text=True, timeout=1.5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    celsius = parse(result.stdout.strip())
                    if 0 < celsius < 150:
                        if cached_idx is None:
                            # Lock in the first method that worked. Future
                            # calls bypass the other two entirely.
                            self._wmi_cpu_method_idx = i
                            logger.info(f"WMI CPU temp via {label}: caching this method")
                        return round(celsius, 1)
            except Exception as e:
                logger.debug(f"WMI {label} method failed: {e}")

        # First-time exhaustive probe failed → permanent giveup. (If we had
        # a cached method and it just failed once, that's a transient blip;
        # we'll retry it next loop without re-probing the others.)
        if cached_idx is None:
            self._wmi_cpu_dead = True
            logger.info("WMI CPU temp: no working source on this system; will not retry")
        return None

    def _demo_data(self):
        try:
            cpu_load = psutil.cpu_percent(interval=None)
        except Exception:
            cpu_load = 10.0

        target_cpu = 32.0 + cpu_load * 0.55 + random.uniform(-1, 1)
        target_gpu = 28.0 + cpu_load * 0.40 + random.uniform(-1.5, 1.5)
        a = 0.15
        self._demo_temps["cpu"] += a * (target_cpu - self._demo_temps["cpu"])
        self._demo_temps["gpu"] += a * (target_gpu - self._demo_temps["gpu"])

        # Per-core from psutil
        cpu_per_core = []
        try:
            per_cpu = psutil.cpu_percent(percpu=True)
            cpu_per_core = [{"name": f"Core #{i+1}", "load": v} for i, v in enumerate(per_cpu)]
        except Exception:
            pass

        # Disk & network
        disk_io = {"read_mb": 0, "write_mb": 0}
        try:
            disk = psutil.disk_io_counters()
            if disk:
                disk_io = {"read_mb": round(disk.read_bytes / 1024 / 1024, 1),
                           "write_mb": round(disk.write_bytes / 1024 / 1024, 1)}
        except Exception:
            pass

        network = {"sent_mb": 0, "recv_mb": 0}
        try:
            net = psutil.net_io_counters()
            if net:
                network = {"sent_mb": round(net.bytes_sent / 1024 / 1024, 1),
                           "recv_mb": round(net.bytes_recv / 1024 / 1024, 1)}
        except Exception:
            pass

        ram_usage = 0.0
        try:
            ram_usage = round(psutil.virtual_memory().percent, 1)
        except Exception:
            pass

        return {
            "cpu_temp": round(self._demo_temps["cpu"], 1),
            "cpu_load": round(cpu_load, 1),
            "cpu_per_core": cpu_per_core,
            "gpu_temp": round(self._demo_temps["gpu"], 1),
            "gpu_hot_spot": None,
            "gpu_load": round(random.uniform(2, 15), 1),
            "gpu_clock_core": round(210 + random.uniform(-10, 50)),
            "gpu_clock_mem": 405,
            "gpu_mem_used": round(800 + random.uniform(-50, 100)),
            "gpu_mem_total": 4096,
            "fan_speeds": self._demo_fans(),
            "ram_usage": ram_usage,
            "storage_temps": [
                {"drive": "NVMe SSD", "sensor": "Composite",
                 "temp": round(35 + random.uniform(-2, 3), 1)},
            ],
            "disk_io": disk_io,
            "network": network,
        }

    def _demo_fans(self):
        fans = []
        for i, (name, base_rpm) in enumerate([("CPU Fan", 800), ("Chassis Fan 1", 600)]):
            pct = self._demo_fan_pct.get(i, 30.0)
            rpm = int(base_rpm + pct * 12)
            fans.append({"name": name, "rpm": rpm, "percent": round(pct, 1)})
        return fans