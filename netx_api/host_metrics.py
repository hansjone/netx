"""Host CPU / memory metrics (Windows + Linux), used by workbench gauges."""

from __future__ import annotations

import os
import platform
import threading
import time
from typing import Any

_CPU_SAMPLE: tuple[float, float, float] | None = None  # (idle, total, mono)
_PSUTIL_PRIMED = False
_CACHE_LOCK = threading.Lock()
_CACHED_HOST: dict[str, Any] | None = None
_CACHED_AT = 0.0
_CACHE_TTL_SEC = 1.5

# Windows Task Manager uses "% Processor Utility" (freq-aware), not "% Processor Time".
# On modern machines Utility is often ~5–10× Time; matching TM requires PDH Utility.
_PDH_LOCK = threading.Lock()
_PDH_QUERY: Any = None
_PDH_COUNTER: Any = None
_PDH_PRIMED = False
_PDH_LAST: float | None = None
_PDH_FMT_DOUBLE = 0x00000200
_PDH_PATH = r"\Processor Information(_Total)\% Processor Utility"


def _clamp_pct(n: float) -> float:
    if n != n:  # NaN
        return 0.0
    return max(0.0, min(100.0, float(n)))


def _windows_cpu_utility_pdh(sample_sec: float = 0.25) -> float | None:
    """Return Task Manager–aligned CPU % via PDH Processor Utility."""
    global _PDH_QUERY, _PDH_COUNTER, _PDH_PRIMED, _PDH_LAST
    if platform.system().lower() != "windows":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    class PDH_FMT_COUNTERVALUE(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [
                ("longValue", wintypes.LONG),
                ("doubleValue", ctypes.c_double),
                ("largeValue", ctypes.c_longlong),
                ("AnsiStringValue", wintypes.LPSTR),
                ("WideStringValue", wintypes.LPWSTR),
            ]

        _anonymous_ = ("u",)
        _fields_ = [("CStatus", wintypes.DWORD), ("u", _U)]

    try:
        pdh = ctypes.WinDLL("pdh")
        pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_ulonglong, ctypes.POINTER(ctypes.c_void_p)]
        pdh.PdhOpenQueryW.restype = wintypes.DWORD
        pdh.PdhAddCounterW.argtypes = [
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.c_ulonglong,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        pdh.PdhAddCounterW.restype = wintypes.DWORD
        pdh.PdhAddEnglishCounterW.argtypes = pdh.PdhAddCounterW.argtypes
        pdh.PdhAddEnglishCounterW.restype = wintypes.DWORD
        pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        pdh.PdhCollectQueryData.restype = wintypes.DWORD
        pdh.PdhGetFormattedCounterValue.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(PDH_FMT_COUNTERVALUE),
        ]
        pdh.PdhGetFormattedCounterValue.restype = wintypes.DWORD
    except Exception:
        return None

    with _PDH_LOCK:
        try:
            if _PDH_QUERY is None:
                h_query = ctypes.c_void_p()
                h_counter = ctypes.c_void_p()
                if pdh.PdhOpenQueryW(None, 0, ctypes.byref(h_query)) != 0:
                    return None
                rc = pdh.PdhAddEnglishCounterW(h_query, _PDH_PATH, 0, ctypes.byref(h_counter))
                if rc != 0:
                    rc = pdh.PdhAddCounterW(h_query, _PDH_PATH, 0, ctypes.byref(h_counter))
                if rc != 0:
                    return None
                _PDH_QUERY = h_query
                _PDH_COUNTER = h_counter
                _PDH_PRIMED = False

    # After prime, CollectQueryData alone is enough (matches Task Manager Utility).
            if pdh.PdhCollectQueryData(_PDH_QUERY) != 0:
                return _PDH_LAST
            if not _PDH_PRIMED:
                time.sleep(max(0.05, min(0.15, float(sample_sec))))
                if pdh.PdhCollectQueryData(_PDH_QUERY) != 0:
                    return _PDH_LAST
                _PDH_PRIMED = True

            val = PDH_FMT_COUNTERVALUE()
            typ = wintypes.DWORD()
            if pdh.PdhGetFormattedCounterValue(
                _PDH_COUNTER, _PDH_FMT_DOUBLE, ctypes.byref(typ), ctypes.byref(val)
            ) != 0:
                return _PDH_LAST
            out = _clamp_pct(float(val.doubleValue))
            _PDH_LAST = out
            return out
        except Exception:
            return _PDH_LAST


def _sample_cpu_percent() -> tuple[float, str]:
    """Return (cpu_percent, cpu_source_tag)."""
    if platform.system().lower() == "windows":
        util = _windows_cpu_utility_pdh(0.25)
        if util is not None:
            return util, "pdh_utility"

    global _PSUTIL_PRIMED
    try:
        import psutil  # type: ignore
    except Exception:
        return 0.0, "none"

    if not _PSUTIL_PRIMED:
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass
        _PSUTIL_PRIMED = True
    try:
        cpu = float(psutil.cpu_percent(interval=0.2))
    except Exception:
        cpu = 0.0
    return _clamp_pct(cpu), "psutil"


def _host_via_psutil() -> dict[str, Any] | None:
    try:
        import psutil  # type: ignore
    except Exception:
        return None

    cpu, cpu_src = _sample_cpu_percent()
    vm = psutil.virtual_memory()
    return {
        "source": f"psutil+{cpu_src}" if cpu_src != "psutil" else "psutil",
        "platform": platform.system().lower() or "unknown",
        "cpu_percent": round(_clamp_pct(cpu), 1),
        "cpu_metric": "processor_utility" if cpu_src == "pdh_utility" else "processor_time",
        "cpu_count": int(psutil.cpu_count() or 0) or None,
        "mem_percent": round(_clamp_pct(float(vm.percent)), 1),
        "mem_used_bytes": int(vm.used),
        "mem_total_bytes": int(vm.total),
        "mem_available_bytes": int(vm.available),
    }


def _read_linux_cpu_times() -> tuple[float, float] | None:
    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            line = f.readline()
    except OSError:
        return None
    if not line.startswith("cpu "):
        return None
    parts = line.split()
    try:
        nums = [float(x) for x in parts[1:]]
    except ValueError:
        return None
    if len(nums) < 4:
        return None
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0.0)  # idle + iowait
    total = sum(nums)
    return idle, total


def _read_linux_mem() -> dict[str, int] | None:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None
    kv: dict[str, int] = {}
    for line in lines:
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        token = rest.strip().split()[0]
        try:
            kv[key] = int(token) * 1024  # kB → bytes
        except ValueError:
            continue
    total = kv.get("MemTotal")
    if not total:
        return None
    available = kv.get("MemAvailable")
    if available is None:
        free = kv.get("MemFree", 0)
        buffers = kv.get("Buffers", 0)
        cached = kv.get("Cached", 0)
        available = free + buffers + cached
    used = max(0, total - available)
    return {"total": total, "available": available, "used": used}


def _linux_cpu_percent() -> float:
    global _CPU_SAMPLE
    now = _read_linux_cpu_times()
    if now is None:
        return 0.0
    idle, total = now
    prev = _CPU_SAMPLE
    if prev is None or (time.monotonic() - prev[2]) < 0.15:
        # Need a meaningful window; sleep then resample.
        time.sleep(0.2)
        again = _read_linux_cpu_times()
        if again is None:
            return 0.0
        idle2, total2 = again
        _CPU_SAMPLE = (idle2, total2, time.monotonic())
        di, dt = idle2 - idle, total2 - total
    else:
        di, dt = idle - prev[0], total - prev[1]
        _CPU_SAMPLE = (idle, total, time.monotonic())
    if dt <= 0:
        return 0.0
    return _clamp_pct((1.0 - (di / dt)) * 100.0)


def _windows_mem() -> dict[str, int] | None:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_uint64),
            ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64),
            ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64),
            ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
        return None
    total = int(stat.ullTotalPhys)
    available = int(stat.ullAvailPhys)
    used = max(0, total - available)
    return {"total": total, "available": available, "used": used, "load": int(stat.dwMemoryLoad)}


def _windows_cpu_times() -> tuple[float, float] | None:
    """Return (idle, total) in 100-ns units via GetSystemTimes."""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    def _ft_to_u64(ft: FILETIME) -> int:
        return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)

    idle = FILETIME()
    kernel = FILETIME()
    user = FILETIME()
    ok = ctypes.windll.kernel32.GetSystemTimes(  # type: ignore[attr-defined]
        ctypes.byref(idle),
        ctypes.byref(kernel),
        ctypes.byref(user),
    )
    if not ok:
        return None
    idle_t = float(_ft_to_u64(idle))
    # Kernel includes idle time on Windows.
    kernel_t = float(_ft_to_u64(kernel))
    user_t = float(_ft_to_u64(user))
    total = kernel_t + user_t
    return idle_t, total


def _windows_cpu_percent() -> float:
    global _CPU_SAMPLE
    first = _windows_cpu_times()
    if first is None:
        return 0.0
    idle, total = first
    prev = _CPU_SAMPLE
    if prev is None or (time.monotonic() - prev[2]) < 0.15:
        time.sleep(0.2)
        again = _windows_cpu_times()
        if again is None:
            return 0.0
        idle2, total2 = again
        _CPU_SAMPLE = (idle2, total2, time.monotonic())
        di, dt = idle2 - idle, total2 - total
    else:
        di, dt = idle - prev[0], total - prev[1]
        _CPU_SAMPLE = (idle, total, time.monotonic())
    if dt <= 0:
        return 0.0
    return _clamp_pct((1.0 - (di / dt)) * 100.0)


def _host_via_stdlib() -> dict[str, Any]:
    system = platform.system().lower()
    out: dict[str, Any] = {
        "source": "stdlib",
        "platform": system or "unknown",
        "cpu_percent": 0.0,
        "mem_percent": 0.0,
        "mem_used_bytes": 0,
        "mem_total_bytes": 0,
        "mem_available_bytes": 0,
    }
    if system == "windows":
        mem = _windows_mem()
        if mem:
            total = mem["total"]
            used = mem["used"]
            available = mem["available"]
            pct = float(mem.get("load") or 0)
            if pct <= 0 and total > 0:
                pct = (used / total) * 100.0
            out.update(
                {
                    "mem_percent": round(_clamp_pct(pct), 1),
                    "mem_used_bytes": used,
                    "mem_total_bytes": total,
                    "mem_available_bytes": available,
                }
            )
        util = _windows_cpu_utility_pdh(0.25)
        if util is not None:
            out["cpu_percent"] = round(util, 1)
            out["cpu_metric"] = "processor_utility"
        else:
            out["cpu_percent"] = round(_windows_cpu_percent(), 1)
            out["cpu_metric"] = "processor_time"
        return out

    # Linux / other Unix with /proc
    if os.path.exists("/proc/meminfo"):
        mem = _read_linux_mem()
        if mem:
            total = mem["total"]
            used = mem["used"]
            available = mem["available"]
            pct = (used / total) * 100.0 if total else 0.0
            out.update(
                {
                    "mem_percent": round(_clamp_pct(pct), 1),
                    "mem_used_bytes": used,
                    "mem_total_bytes": total,
                    "mem_available_bytes": available,
                }
            )
        out["cpu_percent"] = round(_linux_cpu_percent(), 1)
        return out

    out["error"] = "unsupported_platform"
    return out


def _collect_host_metrics_uncached() -> dict[str, Any]:
    via = _host_via_psutil()
    if via is not None:
        return via
    try:
        return _host_via_stdlib()
    except Exception as exc:  # noqa: BLE001
        return {
            "source": "error",
            "platform": platform.system().lower() or "unknown",
            "cpu_percent": 0.0,
            "mem_percent": 0.0,
            "mem_used_bytes": 0,
            "mem_total_bytes": 0,
            "mem_available_bytes": 0,
            "error": str(exc)[:200],
        }


def collect_host_metrics() -> dict[str, Any]:
    """Return host cpu/memory dict; never raises.

    Cached briefly so concurrent /metrics polls do not stack 200ms CPU samples.
    """
    global _CACHED_HOST, _CACHED_AT
    now = time.monotonic()
    with _CACHE_LOCK:
        if _CACHED_HOST is not None and (now - _CACHED_AT) < _CACHE_TTL_SEC:
            return dict(_CACHED_HOST)
    out = _collect_host_metrics_uncached()
    with _CACHE_LOCK:
        _CACHED_HOST = dict(out)
        _CACHED_AT = time.monotonic()
    return out
