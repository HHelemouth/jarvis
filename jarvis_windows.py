"""Windows helpers: launch apps, find their windows, and place them on a given monitor."""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("jarvis")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SW_MAXIMIZE = 3
SW_SHOWMINNOACTIVE = 7
SW_RESTORE = 9
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
GW_OWNER = 4
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002

user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]

MonitorEnumProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MonitorEnumProc, wintypes.LPARAM]
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]


Rect = tuple[int, int, int, int]  # left, top, width, height


def enable_dpi_awareness() -> None:
    """Use real pixel coordinates on every monitor, even with different scaling (125 %, 150 %...)."""
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # per-monitor v2
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


# ---------------------------------------------------------------------------
# Monitors
# ---------------------------------------------------------------------------

def monitors_left_to_right() -> list[Rect]:
    """Work area (screen minus taskbar) of each monitor, sorted from left to right."""
    found: list[Rect] = []

    def _cb(hmon, _hdc, _rect, _data):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcWork
            found.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_cb), 0)
    found.sort(key=lambda r: (r[0], r[1]))
    return found


def monitor_rect(index: int) -> Rect | None:
    mons = monitors_left_to_right()
    if not mons:
        return None
    if index >= len(mons):
        log.warning("Ecran n°%d demande mais seulement %d detecte(s) : j'utilise le dernier.", index + 1, len(mons))
        index = len(mons) - 1
    return mons[index]


def split_rect(rect: Rect, part: str) -> Rect:
    """part: "full", "left", "right", "top" or "bottom"."""
    x, y, w, h = rect
    if part == "left":
        return (x, y, w // 2, h)
    if part == "right":
        return (x + w // 2, y, w - w // 2, h)
    if part == "top":
        return (x, y, w, h // 2)
    if part == "bottom":
        return (x, y + h // 2, w, h - h // 2)
    return rect


# ---------------------------------------------------------------------------
# Windows (the GUI kind)
# ---------------------------------------------------------------------------

def _process_exe_name(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(h)


def _window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def app_windows(exe_names: tuple[str, ...]) -> list[tuple[int, str, int]]:
    """Top-level app windows (hwnd, title, area) belonging to one of the given executables."""
    wanted = {e.lower() for e in exe_names}
    out: list[tuple[int, str, int]] = []

    def _cb(hwnd, _):
        if user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if _process_exe_name(pid.value) not in wanted:
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
        if user32.IsIconic(hwnd):
            area = 10**9  # minimized windows report a tiny rect; still a real window
        out.append((int(hwnd), title, area))
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return out


def wait_for_window(
    exe_names: tuple[str, ...],
    timeout: float,
    exclude: set[int] | None = None,
    title_hint: str | None = None,
    min_area: int = 300 * 250,
) -> int | None:
    """Wait for a (new) main window of one of the executables to appear."""
    exclude = exclude or set()
    hint = (title_hint or "").lower()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        wins = [w for w in app_windows(exe_names) if w[0] not in exclude and w[2] >= min_area]
        if hint:
            hinted = [w for w in wins if hint in w[1].lower()]
            if hinted:
                wins = hinted
            elif time.monotonic() < deadline - timeout / 2:
                # Give the page a moment to set its title before accepting any window.
                time.sleep(0.25)
                continue
        if wins:
            return max(wins, key=lambda w: w[2])[0]
        time.sleep(0.25)
    return None


def _frame_margins(hwnd: int) -> tuple[int, int, int, int]:
    """Invisible resize borders of Windows 10/11 (left, top, right, bottom), so windows touch edges."""
    wr = wintypes.RECT()
    fr = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(wr)):
        return (0, 0, 0, 0)
    try:
        res = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(fr), ctypes.sizeof(fr)
        )
    except (AttributeError, OSError):
        return (0, 0, 0, 0)
    if res != 0:
        return (0, 0, 0, 0)
    return (fr.left - wr.left, fr.top - wr.top, wr.right - fr.right, wr.bottom - fr.bottom)


def place_window(hwnd: int, rect: Rect, maximize: bool = False) -> None:
    if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.15)
    x, y, w, h = rect
    ml, mt, mr, mb = _frame_margins(hwnd)
    user32.SetWindowPos(
        hwnd, None, x - ml, y - mt, w + ml + mr, h + mt + mb, SWP_NOZORDER | SWP_NOACTIVATE
    )
    if maximize:
        user32.ShowWindow(hwnd, SW_MAXIMIZE)


def minimize_window(hwnd: int) -> None:
    user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)


def bring_to_front(hwnd: int) -> None:
    # Windows only lets the active app steal focus; a tap on Alt lifts that restriction.
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)


user32.GetForegroundWindow.restype = wintypes.HWND

VK_CONTROL = 0x11


def press_keys(hwnd: int, *keys: int) -> bool:
    """Type a shortcut (e.g. VK_CONTROL, ord("M")) into this window, only if it really has focus."""
    for _ in range(3):
        bring_to_front(hwnd)
        time.sleep(0.4)
        if (user32.GetForegroundWindow() or 0) == hwnd:
            for k in keys:
                user32.keybd_event(k, 0, 0, 0)
            for k in reversed(keys):
                user32.keybd_event(k, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.2)
            return True
    return False


# ---------------------------------------------------------------------------
# Launching apps
# ---------------------------------------------------------------------------

def _app_path(exe: str) -> str | None:
    """Look up an installed program in the registry (App Paths), like the Run dialog does."""
    import winreg

    key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, key) as k:
                value, _ = winreg.QueryValueEx(k, None)
        except OSError:
            continue
        value = str(value).strip().strip('"')
        if value and os.path.isfile(value):
            return value
    return None


def _windows_apps_alias(exe: str) -> bool:
    local = os.environ.get("LOCALAPPDATA", "")
    return bool(local) and os.path.lexists(os.path.join(local, "Microsoft", "WindowsApps", exe))


def _launch_store_app(aumid: str) -> None:
    subprocess.Popen(["explorer.exe", rf"shell:AppsFolder\{aumid}"])


def edge_executable() -> str | None:
    found = _app_path("msedge.exe") or shutil.which("msedge")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            p = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if p.is_file():
                return str(p)
    return None


EDGE_EXES = ("msedge.exe",)


def open_edge_window(url: str) -> bool:
    edge = edge_executable()
    if not edge:
        log.error("Microsoft Edge introuvable : j'ouvre %s avec le navigateur par defaut.", url)
        os.startfile(url)
        return False
    subprocess.Popen([edge, "--new-window", url])
    return True


TEAMS_EXES = ("ms-teams.exe", "teams.exe")


def launch_teams() -> None:
    if _windows_apps_alias("ms-teams.exe"):
        _launch_store_app("MSTeams_8wekyb3d8bbwe!MSTeams")
        return
    local = os.environ.get("LOCALAPPDATA", "")
    classic = Path(local) / "Microsoft" / "Teams" / "Update.exe"
    if local and classic.is_file():
        subprocess.Popen([str(classic), "--processStart", "Teams.exe"])
        return
    try:
        os.startfile("msteams:")
    except OSError:
        log.error("Teams introuvable sur ce PC.")


OUTLOOK_EXES = ("outlook.exe", "olk.exe")


def classic_outlook_installed() -> bool:
    return _app_path("OUTLOOK.EXE") is not None


def launch_outlook() -> None:
    classic = _app_path("OUTLOOK.EXE")
    if classic:
        subprocess.Popen([classic])
        return
    if _windows_apps_alias("olk.exe"):
        _launch_store_app("Microsoft.OutlookForWindows_8wekyb3d8bbwe!Microsoft.OutlookforWindows")
        return
    try:
        os.startfile("outlookmail:")
    except OSError:
        log.error("Outlook introuvable sur ce PC.")


# ---------------------------------------------------------------------------
# Background mode helpers
# ---------------------------------------------------------------------------

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> float:
    """Time since the last keyboard or mouse input."""
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    ticks = kernel32.GetTickCount() & 0xFFFFFFFF
    return ((ticks - info.dwTime) & 0xFFFFFFFF) / 1000.0


kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]


def process_running(pid: int, exe_name: str) -> bool:
    """True if this PID is alive and is still the given program (PIDs get reused)."""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return False
    finally:
        kernel32.CloseHandle(h)
    return _process_exe_name(pid) == exe_name.lower()
