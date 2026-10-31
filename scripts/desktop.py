"""Windows desktop window. API and worker outlive the UI process."""

import ctypes
import hashlib
import time
from contextlib import contextmanager
from ctypes import wintypes

from backend.config import DATA, PORT, ROOT

TITLE = "FrameFlow" if PORT == 8765 else f"FrameFlow · {PORT}"


class DesktopState:
    def __init__(self):
        self._dirty = False

    def set_dirty(self, dirty: bool):
        # The bridge exposes no files, credentials or general Python execution.
        self._dirty = dirty is True


def entry_url(base):
    # A persistent WebView2 profile may already have an older cached index.html
    # from before no-store was enabled. Give each frontend build its own URL.
    version = hashlib.sha256((ROOT / "frontend" / "dist" / "index.html").read_bytes()).hexdigest()[:16]
    return base.rstrip("/") + "/?build=" + version


@contextmanager
def instance_lock():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    key = hashlib.sha256(str(DATA).casefold().encode()).hexdigest()[:24]
    handle = kernel.CreateMutexW(None, False, f"Local\\SurvivalFlow.Desktop.{key}")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    primary = ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    try:
        yield primary
    finally:
        kernel.CloseHandle(handle)


def focus_existing():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user.FindWindowW.restype = wintypes.HWND
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    # The first launcher may still be starting the services and WebView2.
    for _ in range(150):
        handle = user.FindWindowW(None, TITLE)
        if handle:
            user.ShowWindow(handle, 9)  # SW_RESTORE, including a minimized window.
            user.SetForegroundWindow(handle)
            return
        time.sleep(0.1)


def open_window(base):
    import webview

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SurvivalFlow.Studio")
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    state = DesktopState()
    window = webview.create_window(
        TITLE,
        entry_url(base),
        width=1280,
        height=800,
        min_size=(1040, 680),
        background_color="#14171b",
        text_select=True,
        js_api=state,
    )

    def closing():
        # Do not synchronously evaluate JS from FormClosing: WebView2 completes
        # JS on the same UI thread, so waiting for it here would deadlock.
        if state._dirty:
            answer = ctypes.windll.user32.MessageBoxW(
                None,
                "Есть несохранённые изменения. Закрыть окно без сохранения?",
                TITLE,
                0x134,  # MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 (No).
            )
            return answer == 6
        return True

    window.events.closing += closing
    try:
        webview.start(
            gui="edgechromium",
            private_mode=False,
            storage_path=str(DATA / "desktop-profile"),
            icon=str(ROOT / "assets" / "icons" / "survivalflow-v1.ico"),
        )
    except Exception as error:
        raise RuntimeError(
            "Не удалось открыть окно FrameFlow. Требуется Microsoft Edge WebView2 Runtime. "
            "Подробности: " + str(error)
        ) from error
