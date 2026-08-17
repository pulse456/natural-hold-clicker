from __future__ import annotations

import ctypes
import json
import math
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
import winsound
from ctypes import wintypes
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from tkinter import messagebox, ttk


APP_NAME = "自然长按连点器"
APP_VERSION = "1.2.0"
INJECTED_MARKER = 0xC0DEC11C

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WH_MOUSE_LL = 14
LLMHF_INJECTED = 0x00000001

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040

MOD_NOREPEAT = 0x4000
HOTKEY_TOGGLE_ID = 1
HOTKEY_PANIC_ID = 2
VK_F12 = 0x7B


BUTTON_LABELS = {"left": "鼠标左键", "right": "鼠标右键", "middle": "鼠标中键"}
LABEL_TO_BUTTON = {label: key for key, label in BUTTON_LABELS.items()}
INJECTION_LABELS = {
    "sendinput": "标准模式（推荐）",
    "legacy": "游戏兼容模式",
    "message": "窗口消息模式（旧游戏）",
}
LABEL_TO_INJECTION = {label: key for key, label in INJECTION_LABELS.items()}
HOTKEYS = {f"F{i}": 0x6F + i for i in range(6, 12)}


@dataclass(frozen=True)
class AppConfig:
    clicks_per_minute: int = 420
    jitter_percent: float = 12.0
    hold_threshold_ms: int = 360
    trigger_button: str = "left"
    injection_mode: str = "sendinput"
    toggle_hotkey: str = "F8"
    natural_rhythm: bool = True
    sound_enabled: bool = True
    start_enabled: bool = True

    def validated(self) -> "AppConfig":
        return replace(
            self,
            clicks_per_minute=max(30, min(3000, int(self.clicks_per_minute))),
            jitter_percent=max(0.0, min(50.0, float(self.jitter_percent))),
            hold_threshold_ms=max(150, min(1500, int(self.hold_threshold_ms))),
            trigger_button=self.trigger_button if self.trigger_button in BUTTON_LABELS else "left",
            injection_mode=self.injection_mode if self.injection_mode in INJECTION_LABELS else "sendinput",
            toggle_hotkey=self.toggle_hotkey if self.toggle_hotkey in HOTKEYS else "F8",
            natural_rhythm=bool(self.natural_rhythm),
            sound_enabled=bool(self.sound_enabled),
            start_enabled=bool(self.start_enabled),
        )


def config_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NaturalHoldClicker"
    return base / "config.json"


def load_config() -> AppConfig:
    path = config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = set(AppConfig.__dataclass_fields__)
        return AppConfig(**{key: value for key, value in raw.items() if key in allowed}).validated()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return AppConfig()


def save_config(config: AppConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class HumanRhythm:
    """Produces bounded, correlated intervals instead of machine-flat timing."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self._drift = 0.0

    def next_interval(self, clicks_per_minute: int, jitter_percent: float, natural: bool) -> float:
        base = 60.0 / clicks_per_minute
        jitter = jitter_percent / 100.0
        if jitter <= 0:
            return base

        if natural:
            # Slow rhythm drift makes adjacent intervals related, as they are for a person.
            self._drift = 0.86 * self._drift + self.rng.gauss(0.0, jitter * 0.10)
            self._drift = max(-jitter * 0.42, min(jitter * 0.42, self._drift))
            local = self.rng.gauss(0.0, jitter * 0.34)
            factor = 1.0 + self._drift + local

            # A short hesitation is rare. The normalization keeps the long-run rate close
            # to the requested CPM rather than systematically slowing it down.
            pause_probability = 0.018
            pause = self.rng.uniform(0.30, 0.75) if self.rng.random() < pause_probability else 0.0
            factor = (factor + pause) / (1.0 + pause_probability * 0.525)
        else:
            factor = 1.0 + self.rng.uniform(-jitter, jitter)

        factor = max(1.0 - jitter, min(1.0 + max(jitter, 0.75), factor))
        return max(0.008, base * factor)

    def click_hold_time(self, interval: float, natural: bool) -> float:
        if natural:
            desired = self.rng.triangular(0.026, 0.072, 0.043)
        else:
            desired = 0.035
        return max(0.012, min(desired, interval * 0.42))


class SoundPlayer:
    """Serializes short status tones so rapid hotkey presses never overlap."""

    TONES = {
        "enabled": ((740, 55), (1040, 85)),
        "disabled": ((650, 55), (420, 95)),
        "panic": ((300, 180),),
    }

    def __init__(self) -> None:
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def play(self, tone: str) -> None:
        if tone in self.TONES:
            self._queue.put(tone)

    def close(self) -> None:
        self._queue.put(None)

    def _run(self) -> None:
        while True:
            tone = self._queue.get()
            if tone is None:
                return
            try:
                for frequency, duration in self.TONES[tone]:
                    winsound.Beep(frequency, duration)
            except RuntimeError:
                # Some audio drivers do not expose Beep; status changes still work normally.
                pass


ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


if os.name == "nt":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    LOW_LEVEL_MOUSE_PROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )

    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    user32.mouse_event.argtypes = (
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ULONG_PTR,
    )
    user32.mouse_event.restype = None
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.PostMessageW.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.ScreenToClient.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
    user32.ScreenToClient.restype = wintypes.BOOL
    user32.SetWindowsHookExW.argtypes = (
        ctypes.c_int,
        LOW_LEVEL_MOUSE_PROC,
        wintypes.HINSTANCE,
        wintypes.DWORD,
    )
    user32.SetWindowsHookExW.restype = wintypes.HANDLE
    user32.CallNextHookEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.PostThreadMessageW.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def send_mouse_flag(flag: int, mode: str = "sendinput") -> bool:
    if os.name != "nt":
        return False
    if mode == "legacy":
        user32.mouse_event(flag, 0, 0, 0, INJECTED_MARKER)
        return True
    event = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, flag, 0, INJECTED_MARKER))
    return user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) == 1


def button_flags(button: str) -> tuple[int, int]:
    return {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }[button]


def send_button_event(button: str, down: bool, mode: str, target_window: int = 0) -> bool:
    if mode != "message":
        down_flag, up_flag = button_flags(button)
        return send_mouse_flag(down_flag if down else up_flag, mode)

    if os.name != "nt" or not target_window:
        return False
    message = {
        ("left", True): WM_LBUTTONDOWN,
        ("left", False): WM_LBUTTONUP,
        ("right", True): WM_RBUTTONDOWN,
        ("right", False): WM_RBUTTONUP,
        ("middle", True): WM_MBUTTONDOWN,
        ("middle", False): WM_MBUTTONUP,
    }[(button, down)]
    button_mask = {"left": 0x0001, "right": 0x0002, "middle": 0x0010}[button] if down else 0
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return False
    user32.ScreenToClient(target_window, ctypes.byref(point))
    packed_position = (point.x & 0xFFFF) | ((point.y & 0xFFFF) << 16)
    return bool(user32.PostMessageW(target_window, message, button_mask, packed_position))


def is_running_as_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


class ClickEngine:
    def __init__(self, config: AppConfig, notify) -> None:
        self._config = config
        self._notify = notify
        self._lock = threading.RLock()
        self._enabled = config.start_enabled
        self._physically_held = False
        self._press_token = 0
        self._cancel = threading.Event()
        self._rhythm = HumanRhythm()
        self._worker: threading.Thread | None = None
        self._physical_presses = 0
        self._generated_clicks = 0

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def config(self) -> AppConfig:
        with self._lock:
            return self._config

    def update_config(self, config: AppConfig) -> None:
        with self._lock:
            input_changed = (
                config.trigger_button != self._config.trigger_button
                or config.injection_mode != self._config.injection_mode
            )
            self._config = config
            if input_changed:
                self._press_token += 1
                self._physically_held = False
                self._cancel.set()

    def physical_down(self, button: str) -> None:
        with self._lock:
            if button != self._config.trigger_button or self._physically_held:
                return
            self._physical_presses += 1
            self._notify("stats", self._physical_presses, self._generated_clicks)
            self._physically_held = True
            self._press_token += 1
            token = self._press_token
            self._cancel = threading.Event()
            if not self._enabled:
                return
            config = self._config
            self._worker = threading.Thread(
                target=self._run_press, args=(token, config.trigger_button), daemon=True
            )
            self._worker.start()

    def physical_up(self, button: str) -> None:
        with self._lock:
            if button != self._config.trigger_button:
                return
            self._physically_held = False
            self._press_token += 1
            self._cancel.set()
        self._notify("waiting" if self.enabled else "paused")

    def set_enabled(self, enabled: bool, source: str = "ui") -> None:
        with self._lock:
            if self._enabled == enabled:
                return
            self._enabled = enabled
            self._press_token += 1
            self._cancel.set()
            sound_enabled = self._config.sound_enabled
        self._notify("waiting" if enabled else "paused", source)
        if sound_enabled:
            tone = "enabled" if enabled else ("panic" if source == "panic" else "disabled")
            self._notify("sound", tone)

    def toggle(self, source: str = "hotkey") -> None:
        self.set_enabled(not self.enabled, source)

    def panic(self) -> None:
        self.set_enabled(False, "panic")

    def shutdown(self) -> None:
        with self._lock:
            self._enabled = False
            self._physically_held = False
            self._press_token += 1
            self._cancel.set()

    def _still_active(self, token: int) -> bool:
        with self._lock:
            return self._enabled and self._physically_held and token == self._press_token

    def _run_press(self, token: int, button: str) -> None:
        initial_config = self.config
        threshold = initial_config.hold_threshold_ms / 1000.0
        if self._cancel.wait(threshold) or not self._still_active(token):
            return

        target_window = user32.GetForegroundWindow() if initial_config.injection_mode == "message" else 0
        send_button_event(
            button, False, initial_config.injection_mode, target_window
        )  # Release the native held state before generating clicks.
        if self._cancel.wait(0.018) or not self._still_active(token):
            return
        self._notify("clicking")

        while self._still_active(token):
            started = time.perf_counter()
            config = self.config
            interval = self._rhythm.next_interval(
                config.clicks_per_minute, config.jitter_percent, config.natural_rhythm
            )
            sent = send_button_event(button, True, config.injection_mode, target_window)
            if sent:
                with self._lock:
                    self._generated_clicks += 1
                    stats = (self._physical_presses, self._generated_clicks)
                self._notify("stats", *stats)
            hold_time = self._rhythm.click_hold_time(interval, config.natural_rhythm)
            cancelled = self._cancel.wait(hold_time)
            send_button_event(
                button, False, config.injection_mode, target_window
            )  # Always release, even when paused mid-click.
            if cancelled or not self._still_active(token):
                break
            remaining = max(0.0, interval - (time.perf_counter() - started))
            if self._cancel.wait(remaining):
                break


class GlobalInputMonitor:
    def __init__(self, engine: ClickEngine, notify) -> None:
        self.engine = engine
        self.notify = notify
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._hook = None
        self._callback = None
        self._hotkey_name = engine.config.toggle_hotkey
        self._running = False

    def start(self) -> bool:
        if os.name != "nt":
            self.notify("error", "本程序仅支持 Windows。")
            return False
        self._running = True
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return bool(self._hook)

    def stop(self) -> None:
        self._running = False
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def change_hotkey(self, hotkey_name: str) -> None:
        self._hotkey_name = hotkey_name
        # Restarting is simple and makes hotkey registration failure observable.
        self.stop()
        self._ready = threading.Event()
        self._thread_id = 0
        self._hook = None
        self.start()

    def _message_loop(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()

        def low_level_proc(code, w_param, l_param):
            if code >= 0:
                data = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not (data.flags & LLMHF_INJECTED) and data.dwExtraInfo != INJECTED_MARKER:
                    event_map = {
                        WM_LBUTTONDOWN: ("left", True),
                        WM_LBUTTONUP: ("left", False),
                        WM_RBUTTONDOWN: ("right", True),
                        WM_RBUTTONUP: ("right", False),
                        WM_MBUTTONDOWN: ("middle", True),
                        WM_MBUTTONUP: ("middle", False),
                    }
                    mapped = event_map.get(int(w_param))
                    if mapped:
                        button, down = mapped
                        if down:
                            self.engine.physical_down(button)
                        else:
                            self.engine.physical_up(button)
            return user32.CallNextHookEx(self._hook, code, w_param, l_param)

        self._callback = LOW_LEVEL_MOUSE_PROC(low_level_proc)
        self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._callback, None, 0)
        if not self._hook:
            error = ctypes.get_last_error()
            self.notify("error", f"无法监听鼠标（系统错误 {error}）。")
            self._ready.set()
            return

        toggle_ok = bool(user32.RegisterHotKey(None, HOTKEY_TOGGLE_ID, MOD_NOREPEAT, HOTKEYS[self._hotkey_name]))
        panic_ok = bool(user32.RegisterHotKey(None, HOTKEY_PANIC_ID, MOD_NOREPEAT, VK_F12))
        if not toggle_ok:
            self.notify("error", f"{self._hotkey_name} 已被其他程序占用，请换一个快捷键。")
        if not panic_ok:
            self.notify("error", "F12 已被其他程序占用，紧急停止键暂不可用。")
        self._ready.set()

        message = wintypes.MSG()
        while self._running and user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY:
                if message.wParam == HOTKEY_TOGGLE_ID:
                    self.engine.toggle("hotkey")
                elif message.wParam == HOTKEY_PANIC_ID:
                    self.engine.panic()
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

        user32.UnregisterHotKey(None, HOTKEY_TOGGLE_ID)
        user32.UnregisterHotKey(None, HOTKEY_PANIC_ID)
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
        self._hook = None


class App:
    BG = "#F4F7FB"
    CARD = "#FFFFFF"
    TEXT = "#172033"
    MUTED = "#687386"
    BLUE = "#3366E8"
    GREEN = "#188A64"
    RED = "#C23B47"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.config = load_config()
        self.events: queue.Queue[tuple[str, tuple]] = queue.Queue()
        self.sound_player = SoundPlayer()
        self.engine = ClickEngine(self.config, self.post_event)
        self.monitor = GlobalInputMonitor(self.engine, self.post_event)
        self._status = "waiting" if self.config.start_enabled else "paused"

        self._build_window()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self._drain_events)
        if not self.monitor.start():
            self._set_status("error", "全局鼠标监听启动失败。")

    def _build_window(self) -> None:
        self.root.title(f"{APP_NAME}  {APP_VERSION}")
        self.root.geometry("650x690")
        self.root.minsize(610, 660)
        self.root.configure(bg=self.BG)
        try:
            self.root.iconbitmap(default="")
        except tk.TclError:
            pass

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.CARD)
        style.configure("TLabel", background=self.BG, foreground=self.TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("Card.TLabel", background=self.CARD, foreground=self.TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("Muted.Card.TLabel", background=self.CARD, foreground=self.MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background=self.BG, foreground=self.TEXT, font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Subtitle.TLabel", background=self.BG, foreground=self.MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(14, 9))
        style.configure("Primary.TButton", background=self.BLUE, foreground="white", borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#2857D3")])
        style.configure("TCheckbutton", background=self.CARD, foreground=self.TEXT, font=("Microsoft YaHei UI", 10))
        style.map("TCheckbutton", background=[("active", self.CARD)])
        style.configure("TCombobox", padding=6)
        style.configure("TSpinbox", padding=6)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=(28, 22))
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="自然长按连点器", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="短按保持原样；长按达到阈值后，转换为带自然波动的连续点击。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(3, 16))

        status_card = ttk.Frame(outer, style="Card.TFrame", padding=(18, 15))
        status_card.pack(fill="x", pady=(0, 12))
        status_left = ttk.Frame(status_card, style="Card.TFrame")
        status_left.pack(side="left", fill="x", expand=True)
        self.status_title = ttk.Label(status_left, style="Card.TLabel", font=("Microsoft YaHei UI", 12, "bold"))
        self.status_title.pack(anchor="w")
        self.status_detail = ttk.Label(status_left, style="Muted.Card.TLabel")
        self.status_detail.pack(anchor="w", pady=(3, 0))
        self.toggle_button = ttk.Button(status_card, style="Primary.TButton", command=self.toggle)
        self.toggle_button.pack(side="right")

        settings = ttk.Frame(outer, style="Card.TFrame", padding=(18, 16))
        settings.pack(fill="x")

        self.cpm_var = tk.StringVar(value=str(self.config.clicks_per_minute))
        self.jitter_var = tk.StringVar(value=f"{self.config.jitter_percent:g}")
        self.threshold_var = tk.StringVar(value=str(self.config.hold_threshold_ms))
        self.button_var = tk.StringVar(value=BUTTON_LABELS[self.config.trigger_button])
        self.injection_var = tk.StringVar(value=INJECTION_LABELS[self.config.injection_mode])
        self.hotkey_var = tk.StringVar(value=self.config.toggle_hotkey)
        self.natural_var = tk.BooleanVar(value=self.config.natural_rhythm)
        self.sound_var = tk.BooleanVar(value=self.config.sound_enabled)
        self.start_var = tk.BooleanVar(value=self.config.start_enabled)

        self._setting_row(settings, 0, "点击频率", "每分钟 30–3000 次", self._spin(settings, self.cpm_var, 30, 3000, 10), "次/min")
        self._setting_row(settings, 1, "间隔波动", "以目标间隔为中心的随机范围", self._spin(settings, self.jitter_var, 0, 50, 1), "± %")
        self._setting_row(settings, 2, "长按阈值", "短于该时间仍是普通单击", self._spin(settings, self.threshold_var, 150, 1500, 10), "ms")

        button_box = ttk.Combobox(settings, textvariable=self.button_var, values=list(BUTTON_LABELS.values()), state="readonly", width=13)
        self._setting_row(settings, 3, "触发按键", "建议使用左键；短按不会被改写", button_box, "")

        injection_box = ttk.Combobox(
            settings,
            textvariable=self.injection_var,
            values=list(INJECTION_LABELS.values()),
            state="readonly",
            width=20,
        )
        self._setting_row(settings, 4, "输入模式", "FPS 无响应时依次尝试兼容和窗口消息", injection_box, "")

        hotkey_box = ttk.Combobox(settings, textvariable=self.hotkey_var, values=list(HOTKEYS), state="readonly", width=13)
        self._setting_row(settings, 5, "暂停/继续", "全局快捷键；F12 始终紧急停止", hotkey_box, "")

        options = ttk.Frame(settings, style="Card.TFrame")
        options.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        ttk.Checkbutton(options, text="自然节奏（轻微漂移和低概率短停顿）", variable=self.natural_var).pack(anchor="w")
        ttk.Checkbutton(options, text="状态提示音（开启升调、暂停降调）", variable=self.sound_var).pack(anchor="w", pady=(5, 0))
        ttk.Checkbutton(options, text="下次启动时自动启用", variable=self.start_var).pack(anchor="w", pady=(5, 0))

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(14, 0))
        self.feedback = ttk.Label(footer, text="", style="Subtitle.TLabel")
        self.feedback.pack(side="left")
        ttk.Button(footer, text="保存并应用", style="Primary.TButton", command=self.apply).pack(side="right")
        if not is_running_as_admin():
            ttk.Button(footer, text="管理员重启", command=self.restart_as_admin).pack(side="right", padx=(0, 8))

        self.stats_label = ttk.Label(
            outer,
            text="输入诊断：检测到 0 次物理按下 · 已生成 0 次点击",
            style="Subtitle.TLabel",
        )
        self.stats_label.pack(anchor="w", pady=(13, 0))

        safety = ttk.Label(
            outer,
            text=(
                "安全机制：仅识别真实鼠标输入，程序生成的点击不会再次触发连点。\n"
                f"暂停或松开会强制抬起按键 · 当前以{'管理员' if is_running_as_admin() else '普通'}权限运行。"
            ),
            style="Subtitle.TLabel",
            justify="left",
        )
        safety.pack(anchor="w", pady=(16, 0))
        self._set_status(self._status)

    def _spin(self, parent, variable, start, end, increment):
        return ttk.Spinbox(parent, textvariable=variable, from_=start, to=end, increment=increment, width=11, justify="right")

    def _setting_row(self, parent, row, title, hint, control, suffix) -> None:
        ttk.Label(parent, text=title, style="Card.TLabel", font=("Microsoft YaHei UI", 10, "bold")).grid(
            row=row, column=0, sticky="w", pady=8
        )
        ttk.Label(parent, text=hint, style="Muted.Card.TLabel").grid(row=row, column=1, sticky="w", padx=(12, 10), pady=8)
        control.grid(row=row, column=2, sticky="e", pady=8)
        ttk.Label(parent, text=suffix, style="Muted.Card.TLabel", width=6).grid(row=row, column=3, sticky="w", padx=(7, 0), pady=8)
        parent.columnconfigure(1, weight=1)

    def post_event(self, name: str, *args) -> None:
        self.events.put((name, args))

    def _drain_events(self) -> None:
        try:
            while True:
                name, args = self.events.get_nowait()
                if name in {"waiting", "paused", "clicking"}:
                    self._set_status(name, *(args[:1]))
                elif name == "stats":
                    self.stats_label.configure(
                        text=f"输入诊断：检测到 {args[0]} 次物理按下 · 已生成 {args[1]} 次点击"
                    )
                elif name == "sound":
                    self.sound_player.play(args[0])
                elif name == "error":
                    self._set_status("error", args[0] if args else "发生未知错误。")
        except queue.Empty:
            pass
        self.root.after(50, self._drain_events)

    def _set_status(self, status: str, detail: str | None = None) -> None:
        self._status = status
        hotkey = self.engine.config.toggle_hotkey
        states = {
            "waiting": ("●  已启用", f"等待长按 · {hotkey} 暂停", "暂停"),
            "clicking": ("●  正在连续点击", "松开鼠标立即停止 · F12 紧急停止", "暂停"),
            "paused": ("●  已暂停", f"普通鼠标操作不受影响 · {hotkey} 继续", "继续"),
            "error": ("●  需要处理", detail or "监听启动失败", "重试"),
        }
        title, default_detail, button = states[status]
        self.status_title.configure(text=title, foreground=self.GREEN if status in {"waiting", "clicking"} else self.RED)
        self.status_detail.configure(text=detail or default_detail)
        self.toggle_button.configure(text=button)

    def toggle(self) -> None:
        if self._status == "error":
            if self.monitor.start():
                self._set_status("waiting" if self.engine.enabled else "paused")
            return
        self.engine.toggle("ui")

    def apply(self) -> None:
        try:
            new_config = AppConfig(
                clicks_per_minute=int(self.cpm_var.get()),
                jitter_percent=float(self.jitter_var.get()),
                hold_threshold_ms=int(self.threshold_var.get()),
                trigger_button=LABEL_TO_BUTTON[self.button_var.get()],
                injection_mode=LABEL_TO_INJECTION[self.injection_var.get()],
                toggle_hotkey=self.hotkey_var.get(),
                natural_rhythm=self.natural_var.get(),
                sound_enabled=self.sound_var.get(),
                start_enabled=self.start_var.get(),
            )
            validated = new_config.validated()
            if validated != new_config:
                raise ValueError("数值超出允许范围")
        except (ValueError, KeyError):
            messagebox.showerror("设置无效", "请检查频率、波动和长按阈值是否在允许范围内。")
            return

        old_hotkey = self.config.toggle_hotkey
        self.config = validated
        self.engine.update_config(validated)
        try:
            save_config(validated)
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法保存设置：{exc}")
            return
        if old_hotkey != validated.toggle_hotkey:
            self.monitor.change_hotkey(validated.toggle_hotkey)
        self.feedback.configure(text="设置已保存并立即生效")
        self.root.after(2500, lambda: self.feedback.configure(text=""))
        self._set_status("waiting" if self.engine.enabled else "paused")

    def restart_as_admin(self) -> None:
        if getattr(sys, "frozen", False):
            executable = sys.executable
            parameters = None
        else:
            executable = sys.executable
            parameters = f'"{Path(__file__).resolve()}"'
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, parameters, str(Path.cwd()), 1
        )
        if result > 32:
            self.close()
        else:
            messagebox.showerror("无法提升权限", "管理员启动被取消或被系统阻止。")

    def close(self) -> None:
        self.engine.shutdown()
        self.monitor.stop()
        self.sound_player.close()
        self.root.destroy()


def main() -> None:
    if os.name != "nt":
        raise SystemExit("This application only supports Windows.")
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
