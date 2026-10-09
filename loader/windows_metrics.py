"""Windows counterparts of the Linux /proc and /sys readings, through documented APIs.

Performance counters (PDH) supply the physical-memory lists and the RAPL CPU
package power that Windows exposes as the "Energy Meter" counter set. Process
memory comes from GetProcessMemoryInfo, which is cheap even for a process with
a working set of hundreds of GiB."""
import ctypes
from ctypes import wintypes
import threading
import time

_pdh = ctypes.WinDLL('pdh')
_psapi = ctypes.WinDLL('psapi')
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000
VALID = (0, 1)  # PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA

# Physical memory lists, in Task Manager's terms. "Available" = free + standby.
COUNTERS = {
    'free': r'\Memory\Free & Zero Page List Bytes',
    'standby_core': r'\Memory\Standby Cache Core Bytes',
    'standby_normal': r'\Memory\Standby Cache Normal Priority Bytes',
    'standby_reserve': r'\Memory\Standby Cache Reserve Bytes',
    'modified': r'\Memory\Modified Page List Bytes',
    'cpu_package_mw': r'\Energy Meter(RAPL_Package0_PKG)\Power',
}


class _Value(ctypes.Union):
    _fields_ = [('longValue', ctypes.c_long), ('doubleValue', ctypes.c_double), ('largeValue', ctypes.c_longlong),
                ('AnsiStringValue', ctypes.c_char_p), ('WideStringValue', ctypes.c_wchar_p)]


class _CounterValue(ctypes.Structure):
    _fields_ = [('CStatus', wintypes.DWORD), ('u', _Value)]


class _MemoryCounters(ctypes.Structure):
    """PROCESS_MEMORY_COUNTERS_EX2 (Windows 10 1809 and later)."""
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t),
                ('PrivateUsage', ctypes.c_size_t), ('PrivateWorkingSetSize', ctypes.c_size_t),
                ('SharedCommitUsage', ctypes.c_ulonglong)]


_pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
_pdh.PdhAddEnglishCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
_pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
_pdh.PdhGetFormattedCounterValue.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                             ctypes.POINTER(_CounterValue)]
for _function in (_pdh.PdhOpenQueryW, _pdh.PdhAddEnglishCounterW, _pdh.PdhCollectQueryData, _pdh.PdhGetFormattedCounterValue):
    _function.restype = wintypes.LONG
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD]


class Counters:
    """One PDH query with every counter Inflect reads; absent counters are skipped."""

    RATES = ('cpu_package_mw',)  # Derived from two collections; meaningless over a few milliseconds.

    def __init__(self, paths=COUNTERS):
        self.lock = threading.Lock()
        self.query = wintypes.HANDLE()
        self.handles = {}
        self.values, self.collected = None, time.monotonic()
        if _pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) != 0:
            self.query = None
            return
        for key, path in paths.items():
            handle = wintypes.HANDLE()
            if _pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(handle)) == 0:
                self.handles[key] = handle
        _pdh.PdhCollectQueryData(self.query)

    def read(self, max_age=.5):
        """One collection serves every reader of the same telemetry tick."""
        if not self.query:
            return {}
        with self.lock:
            now = time.monotonic()
            interval = now - self.collected
            if self.values is not None and interval < max_age:
                return dict(self.values)
            if _pdh.PdhCollectQueryData(self.query) != 0:
                return {}
            values = {}
            for key, handle in self.handles.items():
                value = _CounterValue()
                if (_pdh.PdhGetFormattedCounterValue(handle, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100, None, ctypes.byref(value)) == 0
                        and value.CStatus in VALID):
                    values[key] = value.u.doubleValue
            if interval < max_age:
                for key in self.RATES:
                    values.pop(key, None)
            self.values, self.collected = values, now
            return dict(values)


_counters = None


def counters():
    global _counters
    if _counters is None:
        _counters = Counters()
    return _counters


def memory_lists():
    """Free, standby (cache) and modified bytes, or None when a counter is unavailable."""
    values = counters().read()
    keys = ('free', 'standby_core', 'standby_normal', 'standby_reserve', 'modified')
    if not all(key in values for key in keys):
        return None
    return dict(free=int(values['free']), modified=int(values['modified']),
                standby=int(values['standby_core'] + values['standby_normal'] + values['standby_reserve']))


def cpu_package_watts():
    milliwatts = counters().read().get('cpu_package_mw')
    return milliwatts / 1000 if milliwatts is not None else None


def process_counters(pid):
    """Working set, private working set and private commit of one process, or None."""
    handle = _kernel32.OpenProcess(0x1000 | 0x0010, False, pid)  # QUERY_LIMITED_INFORMATION | VM_READ
    if not handle:
        handle = _kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        info = _MemoryCounters()
        info.cb = ctypes.sizeof(info)
        if not _psapi.GetProcessMemoryInfo(handle, ctypes.byref(info), info.cb):
            return None
        return dict(working_set=info.WorkingSetSize, private_working_set=info.PrivateWorkingSetSize,
                    private_commit=info.PrivateUsage)
    finally:
        _kernel32.CloseHandle(handle)
