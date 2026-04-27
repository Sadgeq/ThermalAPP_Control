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

        if self._lhm_available:
            self._init_lhm()
        else:
            self.fan_count = 2
            logger.info("Running in DEMO mode")

    def _init_lhm(self):
        from LibreHardwareMonitor.Hardware import Computer
        self._computer = Computer()
        self._computer.IsCpuEnabled = True
        self._computer.IsGpuEnabled = True
        self._computer.IsMotherboardEnabled = True
        self._computer.IsMemoryEnabled = True
        self._computer.IsControllerEnabled = True
        self._computer.IsStorageEnabled = True
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
        if not self._lhm_available:
            return self._demo_fans()
        try:
            for hw in self._computer.Hardware:
                hw.Update()
                for sub in hw.SubHardware:
                    sub.Update()
            result = []
            for i, fan in enumerate(self._fans):
                rpm = int(fan.Value) if fan.Value is not None else 0
                pct = None
                if i < len(self._fan_controllers):
                    v = self._fan_controllers[i].Value
                    pct = float(v) if v is not None else None
                result.append({"name": str(fan.Name), "rpm": rpm, "percent": pct})
            return result
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
            hw.Update()
            for sub in hw.SubHardware:
                sub.Update()

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

    @staticmethod
    def _wmi_cpu_temp():
        """Fallback: read CPU temp from Windows WMI ACPI thermal zone.
        Tries multiple methods since availability varies by hardware/BIOS."""
        if sys.platform != "win32":
            return None
        import subprocess

        # Method 1: MSAcpi_ThermalZoneTemperature (requires admin, works on most laptops)
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance MSAcpi_ThermalZoneTemperature -Namespace root/wmi -ErrorAction Stop | Select -First 1 -ExpandProperty CurrentTemperature"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                raw = float(result.stdout.strip())
                celsius = (raw / 10.0) - 273.15
                if 0 < celsius < 150:
                    return round(celsius, 1)
            logger.debug(f"WMI MSAcpi method: rc={result.returncode}, stdout='{result.stdout.strip()[:50]}', stderr='{result.stderr.strip()[:80]}'")
        except Exception as e:
            logger.debug(f"WMI MSAcpi method failed: {e}")

        # Method 2: OpenHardwareMonitor WMI namespace (if OHM/LHM exposes it)
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance -Namespace root/OpenHardwareMonitor -ClassName Sensor -ErrorAction Stop | Where-Object {$_.SensorType -eq 'Temperature' -and $_.Name -like '*CPU*'} | Select -First 1 -ExpandProperty Value"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                celsius = float(result.stdout.strip())
                if 0 < celsius < 150:
                    return round(celsius, 1)
        except Exception as e:
            logger.debug(f"WMI OHM method failed: {e}")

        # Method 3: LibreHardwareMonitor WMI namespace
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor -ErrorAction Stop | Where-Object {$_.SensorType -eq 'Temperature' -and $_.Name -like '*CPU*'} | Select -First 1 -ExpandProperty Value"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                celsius = float(result.stdout.strip())
                if 0 < celsius < 150:
                    return round(celsius, 1)
        except Exception as e:
            logger.debug(f"WMI LHM method failed: {e}")

        # Last resort: psutil (only works on Linux with sensors)
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    for entry in entries:
                        if entry.current and 0 < entry.current < 150:
                            return round(entry.current, 1)
        except Exception:
            pass
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