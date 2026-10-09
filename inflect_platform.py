"""The few places where Linux and Windows differ, behind one small interface.

Standard library only: the launcher, setup scripts and server all import it.
Linux behaviour is unchanged; Windows uses documented Win32 equivalents.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

WINDOWS = os.name == 'nt'

if WINDOWS:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
else:
    import fcntl

CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


# Locations ----------------------------------------------------------------------------
def config_home():
    if WINDOWS:
        return Path(os.environ.get('APPDATA') or Path.home() / 'AppData/Roaming')
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')


def data_home():
    if WINDOWS:
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local')
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')


def default_runtime():
    """Private Python environments, engines and state."""
    return data_home() / ('Inflect' if WINDOWS else 'linux-llm-loader')


def venv_python(venv, windowed=False):
    if WINDOWS:
        return Path(venv) / 'Scripts' / ('pythonw.exe' if windowed else 'python.exe')
    return Path(venv) / 'bin/python'


def venv_root(python):
    """The environment directory of a venv interpreter (bin/python or Scripts\\python.exe)."""
    return Path(python).parent.parent


def site_packages(venv):
    pattern = 'Lib/site-packages' if WINDOWS else 'lib/python*/site-packages'
    return sorted(Path(venv).glob(pattern))


def program(path):
    """An executable path with the platform's suffix."""
    path = Path(path)
    return path.with_name(path.name + '.exe') if WINDOWS and path.suffix.lower() != '.exe' else path


def exllama_lock(project):
    """runtime-locks/exllama.json for this platform: shared pins plus the platform's overrides."""
    lock = json.loads((Path(project) / 'runtime-locks/exllama.json').read_text(encoding='utf-8'))
    pinned = lock.pop('platforms')['windows' if WINDOWS else 'linux']
    return {**lock, **pinned}


# File locks -----------------------------------------------------------------------------
def lock(handle, blocking=True):
    """Exclusive lock on an open file, released when it closes.

    Raises BlockingIOError when another process holds it and blocking is False."""
    if not WINDOWS:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    handle.seek(0)
    while True:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if not blocking:
                raise BlockingIOError('The file is locked by another process.') from None
            time.sleep(.1)


# Processes ------------------------------------------------------------------------------
def hidden():
    """Popen/asyncio arguments for a helper command: never open a console window."""
    return {'creationflags': CREATE_NO_WINDOW} if WINDOWS else {}


def owned():
    """Popen/asyncio arguments for a child that a ProcessTree will own and stop as a unit.

    Windows starts it suspended: a venv's Scripts\\python.exe is a launcher that spawns
    the real interpreter at once, and that child must not start before the job holds
    its parent. ProcessTree(pid) assigns the job, then resumes it. Always pair them."""
    if WINDOWS:
        return {'creationflags': CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_SUSPENDED}
    return {'start_new_session': True}


def append_log(path):
    """A log file shared with child processes (binary, append-only).

    Windows: an inherited handle keeps its own file position, so concurrent writers
    would overwrite each other; a FILE_APPEND_DATA-only handle always writes at the end."""
    if not WINDOWS:
        return open(path, 'ab')
    handle = _kernel32.CreateFileW(str(path), 0x0004 | 0x00100000, 0x7, None, 4, 0x80, None)  # append, OPEN_ALWAYS
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    return os.fdopen(msvcrt.open_osfhandle(handle, os.O_APPEND), 'ab')


def spawn_detached(args, **kwargs):
    """Popen a long-lived process that outlives its starter.

    On Windows, also leave a kill-on-close job of the caller (some terminals use
    one) when that job allows it."""
    if WINDOWS:
        flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        try:
            return subprocess.Popen(args, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except PermissionError:
            return subprocess.Popen(args, creationflags=flags, **kwargs)
    return subprocess.Popen(args, start_new_session=True, **kwargs)


def pid_alive(pid):
    if not WINDOWS:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    # os.kill(pid, 0) would send CTRL_C_EVENT on Windows; only query the process.
    handle = _kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED
    if not handle:
        return ctypes.get_last_error() == 5  # Access denied: it exists.
    try:
        return _kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT: still running
    finally:
        _kernel32.CloseHandle(handle)


def large_pages_available():
    """Whether this account holds SeLockMemoryPrivilege ("Lock pages in memory"), which
    Windows requires for large-page allocations. Linux uses transparent huge pages
    without a privilege, so this is a Windows-only question."""
    if not WINDOWS:
        return False
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(_Luid)]
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(_kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
        return False
    try:
        luid = _Luid()
        if not advapi32.LookupPrivilegeValueW(None, 'SeLockMemoryPrivilege', ctypes.byref(luid)):
            return False
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, 3, None, 0, ctypes.byref(size))  # TokenPrivileges
        buffer = (ctypes.c_byte * size.value)()
        if not size.value or not advapi32.GetTokenInformation(token, 3, buffer, size, ctypes.byref(size)):
            return False
        count = wintypes.DWORD.from_buffer(buffer).value
        held = (_LuidAndAttributes * count).from_buffer(buffer, ctypes.sizeof(wintypes.DWORD))
        return any(p.Luid.LowPart == luid.LowPart and p.Luid.HighPart == luid.HighPart for p in held)
    finally:
        _kernel32.CloseHandle(token)


def request_shutdown():
    """Ask this server to stop through uvicorn's signal handler (graceful lifespan exit)."""
    if WINDOWS:
        signal.raise_signal(signal.SIGTERM)
    else:
        os.kill(os.getpid(), signal.SIGTERM)


class ProcessTree:
    """A started child and everything it starts, never unrelated processes.

    POSIX: the child leads its own process group (start it with owned()).
    Windows: a kill-on-close job object holds the child and its descendants,
    so they also end if this manager exits without stopping them. A child
    started suspended by owned() is resumed once the job holds it."""

    def __init__(self, pid):
        self.pid = pid
        self.job = None
        if WINDOWS:
            self.job = _job_for(pid)
            _resume(pid)

    def terminate(self):
        """Polite stop request; Windows has no group-wide equivalent, so it ends the job."""
        if WINDOWS:
            self.kill()
        else:
            os.killpg(self.pid, signal.SIGTERM)

    def kill(self):
        if not WINDOWS:
            os.killpg(self.pid, signal.SIGKILL)
        elif self.job:
            _kernel32.TerminateJobObject(self.job, 1)
        else:
            subprocess.run(['taskkill', '/PID', str(self.pid), '/T', '/F'], capture_output=True, **hidden())

    def wait(self, timeout=30):
        """Until every process of the tree has exited and released its memory.

        Windows: the child is often a venv launcher that exits before the interpreter
        it started, so its exit alone does not mean RAM and VRAM are free again."""
        if not self.job:
            return True
        info = _AccountingInfo()
        deadline = time.monotonic() + timeout
        while (_kernel32.QueryInformationJobObject(self.job, 1, ctypes.byref(info), ctypes.sizeof(info), None)
               and info.ActiveProcesses):
            if time.monotonic() > deadline:
                return False
            time.sleep(.05)
        return True

    def close(self):
        if self.job:
            _kernel32.CloseHandle(self.job)
            self.job = None


if WINDOWS:
    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
            'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD)]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', _BasicLimits), ('IoInfo', _IoCounters),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    class _Luid(ctypes.Structure):
        _fields_ = [('LowPart', wintypes.DWORD), ('HighPart', wintypes.LONG)]

    class _LuidAndAttributes(ctypes.Structure):
        _fields_ = [('Luid', _Luid), ('Attributes', wintypes.DWORD)]

    class _AccountingInfo(ctypes.Structure):
        _fields_ = [('TotalUserTime', ctypes.c_int64), ('TotalKernelTime', ctypes.c_int64),
                    ('ThisPeriodTotalUserTime', ctypes.c_int64), ('ThisPeriodTotalKernelTime', ctypes.c_int64),
                    ('TotalPageFaultCount', wintypes.DWORD), ('TotalProcesses', wintypes.DWORD),
                    ('ActiveProcesses', wintypes.DWORD), ('TotalTerminatedProcesses', wintypes.DWORD)]

    class _Overlapped(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                    ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]

    class _ThreadEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ThreadID', wintypes.DWORD),
                    ('th32OwnerProcessID', wintypes.DWORD), ('tpBasePri', wintypes.LONG),
                    ('tpDeltaPri', wintypes.LONG), ('dwFlags', wintypes.DWORD)]

    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.Thread32First.argtypes = _kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                    ctypes.c_void_p]
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_Overlapped)]
    _kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                          ctypes.c_void_p]

    def _job_for(pid):
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        process = _kernel32.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        try:
            if (process and _kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                    and _kernel32.AssignProcessToJobObject(job, process)):
                return job
        finally:
            if process:
                _kernel32.CloseHandle(process)
        # The child already exited, or a parent job forbids nesting: fall back to taskkill.
        _kernel32.CloseHandle(job)
        return None

    def _resume(pid):
        """Resume the threads of a process started with CREATE_SUSPENDED (no-op otherwise)."""
        snapshot = _kernel32.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot in (None, wintypes.HANDLE(-1).value):
            return
        try:
            entry = _ThreadEntry(dwSize=ctypes.sizeof(_ThreadEntry))
            more = _kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while more:
                if entry.th32OwnerProcessID == pid:
                    thread = _kernel32.OpenThread(0x0002, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
                    if thread:
                        _kernel32.ResumeThread(thread)
                        _kernel32.CloseHandle(thread)
                more = _kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            _kernel32.CloseHandle(snapshot)


def repair_proactor_shutdown():
    """Let uvicorn finish a graceful exit on Windows.

    When a browser resets a connection, CPython's Proactor transport raises from
    socket.shutdown() inside _call_connection_lost and skips Server._detach(), so
    Server.wait_closed(), the last step of uvicorn's shutdown, never returns and
    the engines are never stopped. Complete the skipped cleanup instead."""
    if not WINDOWS:
        return
    from asyncio import proactor_events
    transport = proactor_events._ProactorBasePipeTransport
    original = transport._call_connection_lost
    if getattr(original, 'inflect_repaired', False):
        return

    def call_connection_lost(self, exc):
        try:
            original(self, exc)
        except OSError:
            if self._called_connection_lost:
                raise  # The protocol's own error, after a complete cleanup.
            sock, self._sock = self._sock, None
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            server, self._server = self._server, None
            if server is not None:
                server._detach()
            self._called_connection_lost = True
    call_connection_lost.inflect_repaired = True
    transport._call_connection_lost = call_connection_lost


# Files ----------------------------------------------------------------------------------
O_BINARY = getattr(os, 'O_BINARY', 0)


def pwrite(fd, data, offset):
    """Positional write that never moves a shared file position between threads."""
    if not WINDOWS:
        return os.pwrite(fd, data, offset)
    data = bytes(data)
    overlapped = _Overlapped(Offset=offset & 0xFFFFFFFF, OffsetHigh=offset >> 32)
    written = wintypes.DWORD()
    if not _kernel32.WriteFile(msvcrt.get_osfhandle(fd), data, len(data), ctypes.byref(written), ctypes.byref(overlapped)):
        raise ctypes.WinError(ctypes.get_last_error())
    return written.value


def make_sparse(fd):
    """NTFS zero-fills a file up to each write offset unless it is sparse. Parallel
    ranges of a large download would otherwise stall on gigabytes of zeros."""
    if not WINDOWS:
        return
    returned = wintypes.DWORD()
    _kernel32.DeviceIoControl(msvcrt.get_osfhandle(fd), 0x000900C4, None, 0, None, 0,  # FSCTL_SET_SPARSE
                              ctypes.byref(returned), None)  # Unsupported file systems just stay dense.


def is_link(path):
    path = Path(path)
    return path.is_symlink() or (WINDOWS and path.is_junction())


def remove_link(path):
    path = Path(path)
    if WINDOWS and path.is_junction():
        os.rmdir(path)  # Removes the junction only, never its target's contents.
    elif path.is_symlink():
        path.unlink()


def replace_link(link, target):
    """Point a directory link at target, replacing an earlier link but never a real directory.

    Linux: an atomic symlink swap. Windows: a junction, which needs no administrator
    rights or Developer Mode, swapped by two renames."""
    link, target = Path(link), Path(target).resolve()
    if link.exists() and not is_link(link):
        raise RuntimeError(f'Refusing to replace an unmanaged directory: {link}')
    temporary = link.with_name('.' + link.name + '-next')
    remove_link(temporary)
    if not WINDOWS:
        temporary.symlink_to(target, target_is_directory=True)
        temporary.replace(link)
        return
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(temporary))
    except (ImportError, AttributeError, OSError):
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(temporary), str(target)], check=True,
                       capture_output=True, **hidden())
    if is_link(link):
        previous = link.with_name('.' + link.name + '-previous')
        remove_link(previous)
        os.rename(link, previous)
        os.rename(temporary, link)
        remove_link(previous)
    else:
        os.rename(temporary, link)


def replace_file(source, target, attempts=20):
    """os.replace that waits out brief Windows sharing violations (antivirus or indexer scans)."""
    for attempt in range(attempts):
        try:
            return os.replace(source, target)
        except PermissionError:
            if not WINDOWS or attempt == attempts - 1:
                raise
            time.sleep(.25)


# User messages --------------------------------------------------------------------------
def notify_error(title, message):
    """Show a startup failure when there is no terminal to print it in."""
    if WINDOWS:
        if sys.stderr is None or not sys.stderr.isatty():
            ctypes.WinDLL('user32').MessageBoxW(None, message, title, 0x10)
    elif os.environ.get('DISPLAY'):
        subprocess.run(['notify-send', title, message], check=False)
