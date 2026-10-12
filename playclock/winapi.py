"""Enumerate visible top-level windows with their process exe names (ctypes, no extra deps)."""
import ctypes
import os
from ctypes import wintypes as wt
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.GetWindow.restype = wt.HWND
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wt.HWND
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.GetCurrentProcess.restype = wt.HANDLE
kernel32.SetProcessWorkingSetSize.argtypes = [wt.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
kernel32.SetProcessWorkingSetSize.restype = wt.BOOL

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GW_OWNER = 4


@dataclass
class Window:
    hwnd: int
    pid: int
    exe: str  # lower-case file name, e.g. "ryujinx.exe"
    title: str
    area: int
    minimized: bool
    foreground: bool


# pid -> exe name, kept across polls (pruned to live pids) so processes aren't reopened every few seconds.
_exe_cache: dict[int, str] = {}


def _exe_name(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def list_windows() -> list[Window]:
    """Visible, un-owned, titled top-level windows of other processes."""
    own_pid = os.getpid()
    fg = user32.GetForegroundWindow() or 0
    out: list[Window] = []

    def cb(hwnd, _lparam):
        if not hwnd or not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        if pid.value not in _exe_cache:
            _exe_cache[pid.value] = _exe_name(pid.value)
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        out.append(Window(
            hwnd=int(hwnd), pid=pid.value, exe=_exe_cache[pid.value], title=buf.value,
            area=max(0, r.right - r.left) * max(0, r.bottom - r.top),
            minimized=bool(user32.IsIconic(hwnd)), foreground=int(hwnd) == int(fg),
        ))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    live = {w.pid for w in out}
    for pid in [p for p in _exe_cache if p not in live]:
        del _exe_cache[pid]
    return out


def trim_memory() -> None:
    """Let Windows page out memory this process isn't using (after the window closes / at startup)."""
    kernel32.SetProcessWorkingSetSize(kernel32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1))
