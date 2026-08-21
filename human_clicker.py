from __future__ import annotations

import ctypes
import json
import math
import os
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk
import winsound
from ctypes import wintypes
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from tkinter import messagebox, ttk

from screen_state_detector import ScreenStateDetector, make_dpi_aware


APP_NAME = "自然长按连点器"
APP_VERSION = "1.8.0"
INJECTED_MARKER = 0xC0DEC11C
SINGLE_INSTANCE_MUTEX = "Local\\NaturalHoldClicker.SingleInstance"
ERROR_ALREADY_EXISTS = 183

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
LLMHF_INJECTED = 0x00000001
LLKHF_INJECTED = 0x00000010

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
ACTIVATION_LABELS = {
    "stable": "稳定长按",
    "progressive": "渐进连点（推荐 FPS）",
}
LABEL_TO_ACTIVATION = {label: key for key, label in ACTIVATION_LABELS.items()}
INJECTION_LABELS = {
    "sendinput": "标准模式（推荐）",
    "legacy": "游戏兼容模式",
    "message": "窗口消息模式（旧游戏）",
}
LABEL_TO_INJECTION = {label: key for key, label in INJECTION_LABELS.items()}
HOTKEYS = {f"F{i}": 0x6F + i for i in range(6, 12)}
MAX_PAUSE_BINDINGS = 12
PAUSE_MIN_MS = 10
PAUSE_MAX_MS = 10000
PAUSE_CODE_PATTERN = re.compile(r"^(?:K:\d{1,3}|M:(?:LEFT|RIGHT|MIDDLE|X1|X2))$")

VK_LABELS = {
    0x08: "Backspace",
    0x09: "Tab",
    0x0D: "Enter",
    0x10: "Shift",
    0x11: "Ctrl",
    0x12: "Alt",
    0x13: "Pause",
    0x14: "Caps Lock",
    0x1B: "Esc",
    0x20: "Space",
    0x21: "Page Up",
    0x22: "Page Down",
    0x23: "End",
    0x24: "Home",
    0x25: "←",
    0x26: "↑",
    0x27: "→",
    0x28: "↓",
    0x2D: "Insert",
    0x2E: "Delete",
    0x5B: "左 Win",
    0x5C: "右 Win",
    0x60: "小键盘 0",
    0x61: "小键盘 1",
    0x62: "小键盘 2",
    0x63: "小键盘 3",
    0x64: "小键盘 4",
    0x65: "小键盘 5",
    0x66: "小键盘 6",
    0x67: "小键盘 7",
    0x68: "小键盘 8",
    0x69: "小键盘 9",
    0x6A: "小键盘 *",
    0x6B: "小键盘 +",
    0x6D: "小键盘 -",
    0x6E: "小键盘 .",
    0x6F: "小键盘 /",
    0xA0: "左 Shift",
    0xA1: "右 Shift",
    0xA2: "左 Ctrl",
    0xA3: "右 Ctrl",
    0xA4: "左 Alt",
    0xA5: "右 Alt",
}
MOUSE_PAUSE_LABELS = {
    "M:LEFT": "鼠标左键",
    "M:RIGHT": "鼠标右键",
    "M:MIDDLE": "鼠标中键",
    "M:X1": "鼠标侧键 1",
    "M:X2": "鼠标侧键 2",
}


def keyboard_code(vk_code: int) -> str:
    return f"K:{int(vk_code)}"


def input_code_label(code: str) -> str:
    if code in MOUSE_PAUSE_LABELS:
        return MOUSE_PAUSE_LABELS[code]
    if not code.startswith("K:"):
        return code
    try:
        vk_code = int(code[2:])
    except ValueError:
        return code
    if 0x30 <= vk_code <= 0x39 or 0x41 <= vk_code <= 0x5A:
        return chr(vk_code)
    if 0x70 <= vk_code <= 0x87:
        return f"F{vk_code - 0x6F}"
    return VK_LABELS.get(vk_code, f"按键 VK {vk_code}")


def normalized_pause_bindings(raw_bindings) -> tuple[tuple[str, int, bool], ...]:
    result: list[tuple[str, int, bool]] = []
    positions: dict[str, int] = {}
    for item in raw_bindings or ():
        if not isinstance(item, (list, tuple)) or len(item) not in {2, 3}:
            continue
        code = str(item[0]).upper()
        if not PAUSE_CODE_PATTERN.fullmatch(code):
            continue
        try:
            duration = max(PAUSE_MIN_MS, min(PAUSE_MAX_MS, int(item[1])))
        except (TypeError, ValueError):
            continue
        enabled = bool(item[2]) if len(item) == 3 else True
        entry = (code, duration, enabled)
        if code in positions:
            result[positions[code]] = entry
        elif len(result) < MAX_PAUSE_BINDINGS:
            positions[code] = len(result)
            result.append(entry)
    return tuple(result)


@dataclass(frozen=True)
class AppConfig:
    clicks_per_minute: int = 420
    jitter_percent: float = 12.0
    hold_threshold_ms: int = 360
    trigger_button: str = "left"
    activation_mode: str = "stable"
    injection_mode: str = "sendinput"
    toggle_hotkey: str = "F8"
    natural_rhythm: bool = True
    natural_hesitation: bool = False
    sound_enabled: bool = True
    screen_guard_enabled: bool = False
    visual_clear_frames: int = 3
    pause_bindings: tuple[tuple[str, int, bool], ...] = ()
    start_enabled: bool = True

    def validated(self) -> "AppConfig":
        return replace(
            self,
            clicks_per_minute=max(30, min(3000, int(self.clicks_per_minute))),
            jitter_percent=max(0.0, min(50.0, float(self.jitter_percent))),
            hold_threshold_ms=max(30, min(1500, int(self.hold_threshold_ms))),
            trigger_button=self.trigger_button if self.trigger_button in BUTTON_LABELS else "left",
            activation_mode=self.activation_mode if self.activation_mode in ACTIVATION_LABELS else "stable",
            injection_mode=self.injection_mode if self.injection_mode in INJECTION_LABELS else "sendinput",
            toggle_hotkey=self.toggle_hotkey if self.toggle_hotkey in HOTKEYS else "F8",
            natural_rhythm=bool(self.natural_rhythm),
            natural_hesitation=bool(self.natural_hesitation),
            sound_enabled=bool(self.sound_enabled),
            screen_guard_enabled=bool(self.screen_guard_enabled),
            visual_clear_frames=max(1, min(10, int(self.visual_clear_frames))),
            pause_bindings=normalized_pause_bindings(self.pause_bindings),
            start_enabled=bool(self.start_enabled),
        )


def binding_conflict_message(config: AppConfig) -> str | None:
    toggle_code = keyboard_code(HOTKEYS.get(config.toggle_hotkey, HOTKEYS["F8"]))
    trigger_code = f"M:{config.trigger_button.upper()}"
    for code, _duration, enabled in normalized_pause_bindings(config.pause_bindings):
        if not enabled:
            continue
        if code == keyboard_code(VK_F12):
            return "F12 是紧急停止键，不能同时用作按键暂停规则。"
        if code == toggle_code:
            return f"{config.toggle_hotkey} 是暂停/继续快捷键，不能同时用作按键暂停规则。"
        if code == trigger_code:
            return f"{input_code_label(code)} 是当前连点触发键，不能同时用作按键暂停规则。"
    return None


def config_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NaturalHoldClicker"
    return base / "config.json"


def bundled_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


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

    def next_interval(
        self,
        clicks_per_minute: int,
        jitter_percent: float,
        natural: bool,
        hesitation: bool = False,
    ) -> float:
        base = 60.0 / clicks_per_minute
        jitter = jitter_percent / 100.0
        if jitter <= 0 and not hesitation:
            return base

        if natural and jitter > 0:
            # Slow rhythm drift makes adjacent intervals related, as they are for a person.
            self._drift = 0.86 * self._drift + self.rng.gauss(0.0, jitter * 0.10)
            self._drift = max(-jitter * 0.42, min(jitter * 0.42, self._drift))
            local = self.rng.gauss(0.0, jitter * 0.34)
            factor = 1.0 + self._drift + local
        elif jitter > 0:
            factor = 1.0 + self.rng.uniform(-jitter, jitter)
        else:
            factor = 1.0

        if hesitation:
            # A short hesitation is optional and independent from correlated drift.
            # Normalization keeps the long-run rate close to the requested CPM.
            pause_probability = 0.018
            pause = self.rng.uniform(0.30, 0.75) if self.rng.random() < pause_probability else 0.0
            factor = (factor + pause) / (1.0 + pause_probability * 0.525)

        upper_variation = max(jitter, 0.75) if hesitation else jitter
        factor = max(1.0 - jitter, min(1.0 + upper_variation, factor))
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


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


if os.name == "nt":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    LOW_LEVEL_HOOK_PROC = ctypes.WINFUNCTYPE(
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
        LOW_LEVEL_HOOK_PROC,
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
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL


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


class SingleInstanceGuard:
    def __init__(self, name: str = SINGLE_INSTANCE_MUTEX) -> None:
        self.handle = None
        self.already_running = False
        if os.name == "nt":
            ctypes.set_last_error(0)
            self.handle = kernel32.CreateMutexW(None, False, name)
            self.already_running = bool(self.handle) and ctypes.get_last_error() == ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None


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
        self._screen_blocked = False
        self._screen_block_reason = ""
        self._hold_guarded: bool | None = None
        self._hold_guard_reason = ""
        self._key_pauses: dict[str, tuple[float, str]] = {}
        self._key_pause_timer: threading.Timer | None = None

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def config(self) -> AppConfig:
        with self._lock:
            return self._config

    @property
    def auto_block(self) -> tuple[bool, str]:
        with self._lock:
            return self._block_snapshot_locked()

    @property
    def physically_held(self) -> bool:
        with self._lock:
            return self._physically_held

    @property
    def visual_guard_latched(self) -> bool:
        with self._lock:
            return self._physically_held and self._hold_guarded is True

    def update_config(self, config: AppConfig) -> None:
        pause_bindings_changed = False
        with self._lock:
            input_changed = (
                config.trigger_button != self._config.trigger_button
                or config.activation_mode != self._config.activation_mode
                or config.injection_mode != self._config.injection_mode
            )
            pause_bindings_changed = config.pause_bindings != self._config.pause_bindings
            self._config = config
            if input_changed:
                self._press_token += 1
                self._physically_held = False
                self._hold_guarded = None
                self._hold_guard_reason = ""
                self._cancel.set()
        if pause_bindings_changed:
            self.clear_key_pauses()

    def physical_down(self, button: str) -> bool:
        with self._lock:
            if button != self._config.trigger_button or self._physically_held:
                return False
            self._physical_presses += 1
            self._notify("stats", self._physical_presses, self._generated_clicks)
            self._prune_key_pauses_locked()
            initial_reasons: list[str] = []
            if self._screen_blocked:
                initial_reasons.append(self._screen_block_reason or "界面图标")
            if self._key_pauses:
                labels = sorted({label for _deadline, label in self._key_pauses.values()})
                initial_reasons.append(f"按键预暂停：{' / '.join(labels)}")
            self._hold_guarded = bool(initial_reasons)
            self._hold_guard_reason = (
                f"本次按住锁定：{'；'.join(initial_reasons)}" if initial_reasons else ""
            )
            self._physically_held = True
            self._press_token += 1
            token = self._press_token
            self._cancel = threading.Event()
            blocked, _reason = self._block_snapshot_locked()
            if not self._enabled or blocked:
                return False
            config = self._config
            cancel_event = self._cancel
            pressed_at = time.perf_counter()
            target_window = user32.GetForegroundWindow() if config.injection_mode == "message" else 0

            self._worker = threading.Thread(
                target=self._run_press,
                args=(token, config, target_window, cancel_event, pressed_at),
                daemon=True,
            )
            self._worker.start()
            return False

    def physical_up(self, button: str) -> bool:
        with self._lock:
            if button != self._config.trigger_button:
                return False
            self._physically_held = False
            self._hold_guarded = None
            self._hold_guard_reason = ""
            self._press_token += 1
            self._cancel.set()
            blocked, reason = self._block_snapshot_locked()
            state = "guarded" if self._enabled and blocked else (
                "waiting" if self._enabled else "paused"
            )
        self._notify(state, reason)
        return False

    def set_enabled(self, enabled: bool, source: str = "ui") -> None:
        with self._lock:
            if self._enabled == enabled:
                return
            self._enabled = enabled
            self._press_token += 1
            self._cancel.set()
            sound_enabled = self._config.sound_enabled
            blocked, reason = self._block_snapshot_locked()
            state = "guarded" if enabled and blocked else (
                "waiting" if enabled else "paused"
            )
        self._notify(state, reason if state == "guarded" else source)
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
            self._hold_guarded = None
            self._hold_guard_reason = ""
            self._press_token += 1
            self._cancel.set()
            self._key_pauses.clear()
            if self._key_pause_timer is not None:
                self._key_pause_timer.cancel()
                self._key_pause_timer = None

    def _prune_key_pauses_locked(self, now: float | None = None) -> None:
        now = time.perf_counter() if now is None else now
        expired = [code for code, (deadline, _label) in self._key_pauses.items() if deadline <= now]
        for code in expired:
            self._key_pauses.pop(code, None)

    def _block_snapshot_locked(
        self, now: float | None = None, *, prune: bool = True
    ) -> tuple[bool, str]:
        if prune:
            self._prune_key_pauses_locked(now)
        reasons: list[str] = []
        if self._physically_held and self._hold_guarded is True:
            reasons.append(self._hold_guard_reason or "本次按住锁定保护")
        else:
            if not self._physically_held and self._screen_blocked:
                reasons.append(self._screen_block_reason or "界面图标")
            if self._key_pauses:
                labels = sorted({label for _deadline, label in self._key_pauses.values()})
                reasons.append(f"按键预暂停：{' / '.join(labels)}")
        return bool(reasons), "；".join(reasons)

    def _restart_held_locked(self):
        if not self._enabled or not self._physically_held:
            return None
        self._cancel = threading.Event()
        token = self._press_token
        config = self._config
        target_window = (
            user32.GetForegroundWindow() if config.injection_mode == "message" else 0
        )
        return (
            token,
            config,
            target_window,
            self._cancel,
            time.perf_counter(),
        )

    def _finish_block_change(self, restart_args, state: str, reason: str) -> None:
        if restart_args is not None:
            self._worker = threading.Thread(
                target=self._run_press, args=restart_args, daemon=True
            )
            self._worker.start()
        self._notify(state, reason)

    def _schedule_key_pause_timer_locked(self) -> None:
        if self._key_pause_timer is not None:
            self._key_pause_timer.cancel()
            self._key_pause_timer = None
        self._prune_key_pauses_locked()
        if not self._key_pauses:
            return
        next_deadline = min(deadline for deadline, _label in self._key_pauses.values())
        delay = max(0.001, next_deadline - time.perf_counter())
        self._key_pause_timer = threading.Timer(delay, self._expire_key_pauses)
        self._key_pause_timer.daemon = True
        self._key_pause_timer.start()

    def _expire_key_pauses(self) -> None:
        restart_args = None
        with self._lock:
            before_blocked, before_reason = self._block_snapshot_locked(prune=False)
            self._key_pause_timer = None
            self._prune_key_pauses_locked()
            self._schedule_key_pause_timer_locked()
            blocked, reason = self._block_snapshot_locked()
            if before_blocked and not blocked:
                self._press_token += 1
                self._cancel.set()
                restart_args = self._restart_held_locked()
            if not self._enabled:
                state = "paused"
            elif blocked:
                state = "guarded"
            else:
                state = "waiting"
            changed = (before_blocked, before_reason) != (blocked, reason)
        if changed:
            self._finish_block_change(restart_args, state, reason)

    def trigger_key_pause(self, code: str, label: str, duration_ms: int) -> None:
        restart_args = None
        with self._lock:
            before_blocked, before_reason = self._block_snapshot_locked()
            duration_ms = max(PAUSE_MIN_MS, min(PAUSE_MAX_MS, int(duration_ms)))
            self._key_pauses[code] = (
                time.perf_counter() + duration_ms / 1000.0,
                label,
            )
            self._schedule_key_pause_timer_locked()
            blocked, reason = self._block_snapshot_locked()
            if not before_blocked and blocked:
                self._press_token += 1
                self._cancel.set()
            if not self._enabled:
                state = "paused"
            else:
                state = "guarded"
            changed = (before_blocked, before_reason) != (blocked, reason)
        if changed:
            self._finish_block_change(restart_args, state, reason)

    def clear_key_pauses(self) -> None:
        restart_args = None
        with self._lock:
            before_blocked, before_reason = self._block_snapshot_locked()
            self._key_pauses.clear()
            if self._key_pause_timer is not None:
                self._key_pause_timer.cancel()
                self._key_pause_timer = None
            blocked, reason = self._block_snapshot_locked()
            if before_blocked and not blocked:
                self._press_token += 1
                self._cancel.set()
                restart_args = self._restart_held_locked()
            if not self._enabled:
                state = "paused"
            elif blocked:
                state = "guarded"
            else:
                state = "waiting"
            changed = (before_blocked, before_reason) != (blocked, reason)
        if changed:
            self._finish_block_change(restart_args, state, reason)

    def set_screen_blocked(self, blocked: bool, reason: str = "") -> None:
        restart_args = None
        with self._lock:
            blocked = bool(blocked)
            if self._screen_blocked == blocked and self._screen_block_reason == reason:
                return
            before_blocked, before_reason = self._block_snapshot_locked()
            self._screen_blocked = blocked
            self._screen_block_reason = reason if blocked else ""
            current_blocked, current_reason = self._block_snapshot_locked()
            if before_blocked != current_blocked:
                self._press_token += 1
                self._cancel.set()
                if not current_blocked:
                    restart_args = self._restart_held_locked()

            if not self._enabled:
                state = "paused"
            elif current_blocked:
                state = "guarded"
            else:
                state = "waiting"
            changed = (before_blocked, before_reason) != (current_blocked, current_reason)
        if changed:
            self._finish_block_change(restart_args, state, current_reason)

    def _still_active(self, token: int) -> bool:
        with self._lock:
            blocked, _reason = self._block_snapshot_locked()
            return (
                self._enabled
                and not blocked
                and self._physically_held
                and token == self._press_token
            )

    def _record_generated_click(self) -> None:
        with self._lock:
            self._generated_clicks += 1
            stats = (self._physical_presses, self._generated_clicks)
        self._notify("stats", *stats)

    def _run_press(
        self,
        token: int,
        initial_config: AppConfig,
        target_window: int,
        cancel_event: threading.Event,
        pressed_at: float,
    ) -> None:
        if initial_config.activation_mode == "stable":
            confirmation_delay = initial_config.hold_threshold_ms / 1000.0
        else:  # progressive
            confirmation_delay = 0.050

        if cancel_event.wait(confirmation_delay) or not self._still_active(token):
            return

        native_released = send_button_event(
            initial_config.trigger_button, False, initial_config.injection_mode, target_window
        )  # Release the native held state before generating clicks.
        try:
            if initial_config.activation_mode == "progressive":
                base_interval = 60.0 / initial_config.clicks_per_minute
                first_repeat_at = max(confirmation_delay + 0.008, base_interval * 1.60)
                remaining = max(0.0, first_repeat_at - (time.perf_counter() - pressed_at))
                if cancel_event.wait(remaining) or not self._still_active(token):
                    return
            else:
                if cancel_event.wait(0.005) or not self._still_active(token):
                    return
            self._notify("clicking")

            progressive_started = time.perf_counter()
            while self._still_active(token):
                started = time.perf_counter()
                config = self.config
                interval = self._rhythm.next_interval(
                    config.clicks_per_minute,
                    config.jitter_percent,
                    config.natural_rhythm,
                    config.natural_hesitation,
                )
                if initial_config.activation_mode == "progressive":
                    ramp = max(0.0, 1.0 - (started - progressive_started) / 0.350)
                    interval *= 1.0 + 0.65 * ramp
                sent = send_button_event(
                    initial_config.trigger_button, True, initial_config.injection_mode, target_window
                )
                if sent:
                    self._record_generated_click()
                hold_time = self._rhythm.click_hold_time(interval, config.natural_rhythm)
                cancelled = cancel_event.wait(hold_time)
                send_button_event(
                    initial_config.trigger_button, False, initial_config.injection_mode, target_window
                )  # Always release, even when paused mid-click.
                if cancelled or not self._still_active(token):
                    break
                remaining = max(0.0, interval - (time.perf_counter() - started))
                if cancel_event.wait(remaining):
                    break
        finally:
            if native_released:
                with self._lock:
                    blocked, _reason = self._block_snapshot_locked()
                    restore_hold = self._enabled and blocked and self._physically_held
                if restore_hold:
                    send_button_event(
                        initial_config.trigger_button,
                        True,
                        initial_config.injection_mode,
                        target_window,
                    )

class GlobalInputMonitor:
    def __init__(self, engine: ClickEngine, notify) -> None:
        self.engine = engine
        self.notify = notify
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._mouse_hook = None
        self._keyboard_hook = None
        self._mouse_callback = None
        self._keyboard_callback = None
        self._hotkey_name = engine.config.toggle_hotkey
        self._running = False
        self._input_lock = threading.Lock()
        self._capture_next = False
        self._pressed_inputs: set[str] = set()
        self._suppressed_inputs: set[str] = set()

    def start(self) -> bool:
        if os.name != "nt":
            self.notify("error", "本程序仅支持 Windows。")
            return False
        self._running = True
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return bool(self._mouse_hook and self._keyboard_hook)

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
        self._mouse_hook = None
        self._keyboard_hook = None
        self.start()

    def begin_binding_capture(self) -> None:
        with self._input_lock:
            self._capture_next = True

    def cancel_binding_capture(self) -> None:
        with self._input_lock:
            self._capture_next = False

    def _handle_input_event(self, code: str, label: str, down: bool) -> bool:
        with self._input_lock:
            if down:
                if code in self._pressed_inputs:
                    return code in self._suppressed_inputs
                self._pressed_inputs.add(code)
                if self._capture_next:
                    self._capture_next = False
                    self._suppressed_inputs.add(code)
                    self.notify("binding_captured", code, label)
                    return True
            else:
                self._pressed_inputs.discard(code)
                if code in self._suppressed_inputs:
                    self._suppressed_inputs.discard(code)
                    return True

        if down:
            for binding_code, duration_ms, enabled in self.engine.config.pause_bindings:
                if enabled and binding_code == code:
                    self.engine.trigger_key_pause(code, label, duration_ms)
                    break
        return False

    def _message_loop(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()

        def mouse_proc(code, w_param, l_param):
            if code >= 0:
                data = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not (data.flags & LLMHF_INJECTED) and data.dwExtraInfo != INJECTED_MARKER:
                    event_map = {
                        WM_LBUTTONDOWN: ("left", "M:LEFT", "鼠标左键", True),
                        WM_LBUTTONUP: ("left", "M:LEFT", "鼠标左键", False),
                        WM_RBUTTONDOWN: ("right", "M:RIGHT", "鼠标右键", True),
                        WM_RBUTTONUP: ("right", "M:RIGHT", "鼠标右键", False),
                        WM_MBUTTONDOWN: ("middle", "M:MIDDLE", "鼠标中键", True),
                        WM_MBUTTONUP: ("middle", "M:MIDDLE", "鼠标中键", False),
                    }
                    mapped = event_map.get(int(w_param))
                    if int(w_param) in {WM_XBUTTONDOWN, WM_XBUTTONUP}:
                        side = (int(data.mouseData) >> 16) & 0xFFFF
                        if side in {1, 2}:
                            mapped = (
                                None,
                                f"M:X{side}",
                                f"鼠标侧键 {side}",
                                int(w_param) == WM_XBUTTONDOWN,
                            )
                    if mapped:
                        button, input_code, label, down = mapped
                        suppress = self._handle_input_event(input_code, label, down)
                        if not suppress and button is not None:
                            if down:
                                suppress = self.engine.physical_down(button)
                            else:
                                suppress = self.engine.physical_up(button)
                        if suppress:
                            return 1
            return user32.CallNextHookEx(self._mouse_hook, code, w_param, l_param)

        def keyboard_proc(code, w_param, l_param):
            if code >= 0:
                data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not (data.flags & LLKHF_INJECTED) and data.dwExtraInfo != INJECTED_MARKER:
                    message = int(w_param)
                    if message in {WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP}:
                        input_code = keyboard_code(int(data.vkCode))
                        down = message in {WM_KEYDOWN, WM_SYSKEYDOWN}
                        if self._handle_input_event(
                            input_code, input_code_label(input_code), down
                        ):
                            return 1
            return user32.CallNextHookEx(self._keyboard_hook, code, w_param, l_param)

        self._mouse_callback = LOW_LEVEL_HOOK_PROC(mouse_proc)
        self._keyboard_callback = LOW_LEVEL_HOOK_PROC(keyboard_proc)
        self._mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._mouse_callback, None, 0
        )
        self._keyboard_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._keyboard_callback, None, 0
        )
        if not self._mouse_hook or not self._keyboard_hook:
            error = ctypes.get_last_error()
            if self._mouse_hook:
                user32.UnhookWindowsHookEx(self._mouse_hook)
            if self._keyboard_hook:
                user32.UnhookWindowsHookEx(self._keyboard_hook)
            self._mouse_hook = None
            self._keyboard_hook = None
            self.notify("error", f"无法监听键盘或鼠标（系统错误 {error}）。")
            self._ready.set()
            return

        toggle_ok = bool(user32.RegisterHotKey(None, HOTKEY_TOGGLE_ID, MOD_NOREPEAT, HOTKEYS[self._hotkey_name]))
        panic_ok = bool(user32.RegisterHotKey(None, HOTKEY_PANIC_ID, MOD_NOREPEAT, VK_F12))
        if not (toggle_ok and panic_ok):
            # Do not leave mouse injection armed when its safety hotkeys are unavailable.
            self.engine.set_enabled(False, "hotkey_conflict")
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
        if self._mouse_hook:
            user32.UnhookWindowsHookEx(self._mouse_hook)
        if self._keyboard_hook:
            user32.UnhookWindowsHookEx(self._keyboard_hook)
        self._mouse_hook = None
        self._keyboard_hook = None


class App:
    BG = "#F4F7FB"
    CARD = "#FFFFFF"
    TEXT = "#172033"
    MUTED = "#687386"
    BLUE = "#3366E8"
    GREEN = "#188A64"
    RED = "#C23B47"

    def __init__(self, root: tk.Tk, instance_guard: SingleInstanceGuard | None = None) -> None:
        self.root = root
        self.instance_guard = instance_guard
        self.config = load_config()
        self.events: queue.Queue[tuple[str, tuple]] = queue.Queue()
        self.sound_player = SoundPlayer()
        self.engine = ClickEngine(self.config, self.post_event)
        self.monitor = GlobalInputMonitor(self.engine, self.post_event)
        self.screen_detector: ScreenStateDetector | None = None
        self.pause_rule_rows: list[dict] = []
        self._capturing_rule: dict | None = None
        self._status = "waiting" if self.config.start_enabled else "paused"

        self._build_window()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self._drain_events)
        if not self.monitor.start():
            self._set_status("error", "全局鼠标监听启动失败。")
        if self.config.screen_guard_enabled:
            self.root.after(100, self._start_screen_detector)

    def _build_window(self) -> None:
        self.root.title(f"{APP_NAME}  {APP_VERSION}")
        self.root.geometry("720x900")
        self.root.minsize(670, 650)
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
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, background=self.BG, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        outer = ttk.Frame(self.canvas, padding=(28, 22))
        self.canvas_window = self.canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self.canvas_window, width=event.width),
        )
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)

        ttk.Label(outer, text="自然长按连点器", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="两种触发策略，将鼠标按住转换为带自然波动的连续点击。",
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
        self.activation_var = tk.StringVar(value=ACTIVATION_LABELS[self.config.activation_mode])
        self.injection_var = tk.StringVar(value=INJECTION_LABELS[self.config.injection_mode])
        self.hotkey_var = tk.StringVar(value=self.config.toggle_hotkey)
        self.natural_var = tk.BooleanVar(value=self.config.natural_rhythm)
        self.hesitation_var = tk.BooleanVar(value=self.config.natural_hesitation)
        self.sound_var = tk.BooleanVar(value=self.config.sound_enabled)
        self.screen_guard_var = tk.BooleanVar(value=self.config.screen_guard_enabled)
        self.visual_clear_frames_var = tk.StringVar(value=str(self.config.visual_clear_frames))
        self.start_var = tk.BooleanVar(value=self.config.start_enabled)

        self._setting_row(settings, 0, "点击频率", "每分钟 30–3000 次", self._spin(settings, self.cpm_var, 30, 3000, 10), "次/min")
        self._setting_row(settings, 1, "间隔波动", "以目标间隔为中心的随机范围", self._spin(settings, self.jitter_var, 0, 50, 1), "± %")
        self._setting_row(
            settings,
            2,
            "长按阈值",
            "仅稳定长按使用；最低 30ms，低于下限会自动修正",
            self._spin(settings, self.threshold_var, 30, 1500, 10),
            "ms",
        )

        activation_box = ttk.Combobox(
            settings,
            textvariable=self.activation_var,
            values=list(ACTIVATION_LABELS.values()),
            state="readonly",
            width=20,
        )
        self._setting_row(settings, 3, "触发策略", "稳定按阈值触发；渐进由慢到快", activation_box, "")

        button_box = ttk.Combobox(settings, textvariable=self.button_var, values=list(BUTTON_LABELS.values()), state="readonly", width=13)
        self._setting_row(settings, 4, "触发按键", "普通点按保持原样，按住后才开始连点", button_box, "")

        injection_box = ttk.Combobox(
            settings,
            textvariable=self.injection_var,
            values=list(INJECTION_LABELS.values()),
            state="readonly",
            width=20,
        )
        self._setting_row(settings, 5, "输入模式", "FPS 无响应时依次尝试兼容和窗口消息", injection_box, "")

        hotkey_box = ttk.Combobox(settings, textvariable=self.hotkey_var, values=list(HOTKEYS), state="readonly", width=13)
        self._setting_row(settings, 6, "暂停/继续", "全局快捷键；F12 始终紧急停止", hotkey_box, "")

        self._setting_row(
            settings,
            7,
            "图标消失确认",
            "两种图标连续未检出多少帧后恢复；推荐 3",
            self._spin(settings, self.visual_clear_frames_var, 1, 10, 1),
            "帧",
        )

        options = ttk.Frame(settings, style="Card.TFrame")
        options.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        ttk.Checkbutton(options, text="自然节奏漂移（相邻点击轻微关联）", variable=self.natural_var).pack(anchor="w")
        ttk.Checkbutton(
            options,
            text="低概率短停顿（默认关闭；可能产生可感知的节奏空隙）",
            variable=self.hesitation_var,
        ).pack(anchor="w", pady=(5, 0))
        ttk.Checkbutton(options, text="状态提示音（开启升调、暂停降调）", variable=self.sound_var).pack(anchor="w", pady=(5, 0))
        ttk.Checkbutton(
            options,
            text="识别背包时钟 / 技能鼠标图标时静默暂停连点（40 Hz）",
            variable=self.screen_guard_var,
        ).pack(anchor="w", pady=(5, 0))
        ttk.Checkbutton(options, text="下次启动时自动启用", variable=self.start_var).pack(anchor="w", pady=(5, 0))

        pause_card = ttk.Frame(outer, style="Card.TFrame", padding=(18, 16))
        pause_card.pack(fill="x", pady=(12, 0))
        pause_header = ttk.Frame(pause_card, style="Card.TFrame")
        pause_header.pack(fill="x")
        ttk.Label(
            pause_header,
            text="按键预暂停规则",
            style="Card.TLabel",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side="left")
        ttk.Button(pause_header, text="＋ 添加按键", command=self._add_pause_rule).pack(side="right")
        ttk.Label(
            pause_card,
            text="点击按键输入框后直接按键；支持键盘、鼠标中键和侧键。每个按键可设置独立暂停时间。",
            style="Muted.Card.TLabel",
            wraplength=620,
            justify="left",
        ).pack(anchor="w", pady=(5, 10))
        self.pause_rules_container = ttk.Frame(pause_card, style="Card.TFrame")
        self.pause_rules_container.pack(fill="x")
        self._replace_pause_rule_rows(self.config.pause_bindings)

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

        self.guard_label = ttk.Label(
            outer,
            text="界面检测：未启用",
            style="Subtitle.TLabel",
        )
        self.guard_label.pack(anchor="w", pady=(5, 0))

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

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

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
                if name in {"waiting", "paused", "clicking", "guarded"}:
                    self._set_status(name, *(args[:1]))
                elif name == "stats":
                    self.stats_label.configure(
                        text=f"输入诊断：检测到 {args[0]} 次物理按下 · 已生成 {args[1]} 次点击"
                    )
                elif name == "sound":
                    self.sound_player.play(args[0])
                elif name == "error":
                    self._set_status("error", args[0] if args else "发生未知错误。")
                elif name == "guard_status":
                    self.guard_label.configure(text=f"界面检测：{args[0]}")
                elif name == "guard_state":
                    blocked, reason, mouse_score, clock_score, ignored_for_hold = args
                    if ignored_for_hold:
                        text = "检测到图标 · 本次按住已锁定连点"
                    elif blocked:
                        text = f"已静默暂停 · {reason}"
                    else:
                        text = "监控中 · 未发现阻断图标"
                    self.guard_label.configure(
                        text=(
                            f"界面检测：{text}"
                            f"（鼠标 {mouse_score:.3f} / 时钟 {clock_score:.3f}）"
                        )
                    )
                elif name == "guard_error":
                    self.guard_label.configure(text=f"界面检测：已停止 · {args[0]}")
                elif name == "binding_captured":
                    self._accept_binding_capture(args[0], args[1])
        except queue.Empty:
            pass
        self.root.after(50, self._drain_events)

    def _set_status(self, status: str, detail: str | None = None) -> None:
        self._status = status
        hotkey = self.engine.config.toggle_hotkey
        states = {
            "waiting": ("●  已启用", f"等待长按 · {hotkey} 暂停", "暂停"),
            "clicking": ("●  正在连续点击", "松开鼠标立即停止 · F12 紧急停止", "暂停"),
            "guarded": ("●  自动连点已临时抑制", detail or "检测到无需连点的界面状态", "暂停"),
            "paused": ("●  已暂停", f"普通鼠标操作不受影响 · {hotkey} 继续", "继续"),
            "error": ("●  需要处理", detail or "监听启动失败", "重试"),
        }
        title, default_detail, button = states[status]
        self.status_title.configure(
            text=title,
            foreground=self.GREEN if status in {"waiting", "clicking"} else self.RED,
        )
        self.status_detail.configure(text=detail or default_detail)
        self.toggle_button.configure(text=button)

    def toggle(self) -> None:
        if self._status == "error":
            if self.monitor.start():
                self._set_status("waiting" if self.engine.enabled else "paused")
            return
        self.engine.toggle("ui")

    def _replace_pause_rule_rows(self, bindings) -> None:
        self.monitor.cancel_binding_capture()
        self._capturing_rule = None
        self.pause_rule_rows = []
        for code, duration, enabled in normalized_pause_bindings(bindings):
            self.pause_rule_rows.append(
                {
                    "code": code,
                    "key_var": tk.StringVar(value=input_code_label(code)),
                    "duration_var": tk.StringVar(value=str(duration)),
                    "enabled_var": tk.BooleanVar(value=enabled),
                }
            )
        self._render_pause_rules()

    def _render_pause_rules(self) -> None:
        for child in self.pause_rules_container.winfo_children():
            child.destroy()
        if not self.pause_rule_rows:
            ttk.Label(
                self.pause_rules_container,
                text="尚未添加规则。按键暂停和图标检测可以同时使用。",
                style="Muted.Card.TLabel",
            ).pack(anchor="w", pady=(2, 4))
            return

        headings = ttk.Frame(self.pause_rules_container, style="Card.TFrame")
        headings.pack(fill="x", pady=(0, 4))
        ttk.Label(headings, text="启用", style="Muted.Card.TLabel", width=5).grid(row=0, column=0)
        ttk.Label(headings, text="按键（点击后录入）", style="Muted.Card.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(headings, text="暂停时间", style="Muted.Card.TLabel").grid(row=0, column=2, sticky="e")
        headings.columnconfigure(1, weight=1)

        for index, row in enumerate(self.pause_rule_rows):
            line = ttk.Frame(self.pause_rules_container, style="Card.TFrame")
            line.pack(fill="x", pady=3)
            ttk.Checkbutton(line, variable=row["enabled_var"]).grid(row=0, column=0, padx=(2, 8))
            key_entry = ttk.Entry(
                line,
                textvariable=row["key_var"],
                state="readonly",
                width=24,
            )
            key_entry.grid(row=0, column=1, sticky="ew")
            key_entry.bind("<Button-1>", lambda _event, item=row: self._begin_binding_capture(item))
            duration = ttk.Spinbox(
                line,
                textvariable=row["duration_var"],
                from_=PAUSE_MIN_MS,
                to=PAUSE_MAX_MS,
                increment=10,
                width=8,
                justify="right",
            )
            duration.grid(row=0, column=2, padx=(10, 5))
            ttk.Label(line, text="ms", style="Muted.Card.TLabel").grid(row=0, column=3)
            ttk.Button(
                line,
                text="删除",
                command=lambda item=row: self._delete_pause_rule(item),
            ).grid(row=0, column=4, padx=(10, 0))
            line.columnconfigure(1, weight=1)

    def _add_pause_rule(self) -> None:
        if len(self.pause_rule_rows) >= MAX_PAUSE_BINDINGS:
            messagebox.showinfo("规则已满", f"最多可以添加 {MAX_PAUSE_BINDINGS} 条按键暂停规则。")
            return
        row = {
            "code": "",
            "key_var": tk.StringVar(value="点击这里，然后按键"),
            "duration_var": tk.StringVar(value="250"),
            "enabled_var": tk.BooleanVar(value=True),
        }
        self.pause_rule_rows.append(row)
        self._render_pause_rules()

    def _delete_pause_rule(self, row: dict) -> None:
        if self._capturing_rule is row:
            self.monitor.cancel_binding_capture()
            self._capturing_rule = None
        if row in self.pause_rule_rows:
            self.pause_rule_rows.remove(row)
        self._render_pause_rules()

    def _begin_binding_capture(self, row: dict) -> None:
        if self._capturing_rule is not None and self._capturing_rule is not row:
            previous = self._capturing_rule
            previous["key_var"].set(
                input_code_label(previous["code"])
                if previous["code"]
                else "点击这里，然后按键"
            )
        self._capturing_rule = row
        row["key_var"].set("请按键盘键或鼠标键…")
        self.monitor.begin_binding_capture()
        self.feedback.configure(text="正在捕获下一次按键；该次输入不会传给游戏")

    def _accept_binding_capture(self, code: str, label: str) -> None:
        row = self._capturing_rule
        self._capturing_rule = None
        if row is None:
            return
        duplicate = next(
            (item for item in self.pause_rule_rows if item is not row and item["code"] == code),
            None,
        )
        toggle_code = keyboard_code(HOTKEYS.get(self.hotkey_var.get(), HOTKEYS["F8"]))
        trigger_code = f"M:{LABEL_TO_BUTTON.get(self.button_var.get(), 'left').upper()}"
        if duplicate is not None:
            error = f"{label} 已经存在于另一条规则中。"
        elif code == keyboard_code(VK_F12):
            error = "F12 是紧急停止键，不能绑定。"
        elif code == toggle_code:
            error = f"{self.hotkey_var.get()} 是暂停/继续快捷键，不能绑定。"
        elif code == trigger_code:
            error = f"{label} 是当前连点触发键，不能绑定。"
        else:
            error = ""

        if error:
            row["key_var"].set(
                input_code_label(row["code"]) if row["code"] else "点击这里，然后按键"
            )
            self.feedback.configure(text=error)
            self.root.after(3500, lambda: self.feedback.configure(text=""))
            return
        row["code"] = code
        row["key_var"].set(label)
        self.feedback.configure(text=f"已识别：{label}；设置暂停时间后点击“保存并应用”")
        self.root.after(3500, lambda: self.feedback.configure(text=""))

    def apply(self) -> None:
        try:
            pause_bindings = []
            for row in self.pause_rule_rows:
                if not row["code"]:
                    if row["enabled_var"].get():
                        raise ValueError("存在尚未录入按键的规则")
                    continue
                pause_bindings.append(
                    (
                        row["code"],
                        int(row["duration_var"].get()),
                        row["enabled_var"].get(),
                    )
                )
            new_config = AppConfig(
                clicks_per_minute=int(self.cpm_var.get()),
                jitter_percent=float(self.jitter_var.get()),
                hold_threshold_ms=int(self.threshold_var.get()),
                trigger_button=LABEL_TO_BUTTON[self.button_var.get()],
                activation_mode=LABEL_TO_ACTIVATION[self.activation_var.get()],
                injection_mode=LABEL_TO_INJECTION[self.injection_var.get()],
                toggle_hotkey=self.hotkey_var.get(),
                natural_rhythm=self.natural_var.get(),
                natural_hesitation=self.hesitation_var.get(),
                sound_enabled=self.sound_var.get(),
                screen_guard_enabled=self.screen_guard_var.get(),
                visual_clear_frames=int(self.visual_clear_frames_var.get()),
                pause_bindings=tuple(pause_bindings),
                start_enabled=self.start_var.get(),
            )
            conflict = binding_conflict_message(new_config)
            if conflict:
                messagebox.showerror("按键冲突", conflict)
                return
            validated = new_config.validated()
        except (ValueError, KeyError):
            messagebox.showerror(
                "设置无效",
                "请检查频率、波动、长按阈值、按键暂停时间，并确认每条启用规则都已录入按键。",
            )
            return

        old_hotkey = self.config.toggle_hotkey
        old_screen_guard = self.config.screen_guard_enabled
        old_visual_clear_frames = self.config.visual_clear_frames
        self.config = validated
        self.engine.update_config(validated)
        try:
            save_config(validated)
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法保存设置：{exc}")
            return
        if old_hotkey != validated.toggle_hotkey:
            self.monitor.change_hotkey(validated.toggle_hotkey)
        self.cpm_var.set(str(validated.clicks_per_minute))
        self.jitter_var.set(f"{validated.jitter_percent:g}")
        self.threshold_var.set(str(validated.hold_threshold_ms))
        self.visual_clear_frames_var.set(str(validated.visual_clear_frames))
        self._replace_pause_rule_rows(validated.pause_bindings)
        if old_screen_guard != validated.screen_guard_enabled:
            if validated.screen_guard_enabled:
                self._start_screen_detector()
            else:
                self._stop_screen_detector()
        elif (
            validated.screen_guard_enabled
            and old_visual_clear_frames != validated.visual_clear_frames
            and self.screen_detector is not None
        ):
            self.screen_detector.set_clear_frames(validated.visual_clear_frames)
        self.feedback.configure(text="设置已保存并立即生效")
        self.root.after(2500, lambda: self.feedback.configure(text=""))
        blocked, reason = self.engine.auto_block
        self._set_status(
            "guarded" if self.engine.enabled and blocked else (
                "waiting" if self.engine.enabled else "paused"
            ),
            reason or None,
        )

    def _start_screen_detector(self) -> None:
        if self.screen_detector is not None:
            return
        self.guard_label.configure(text="界面检测：正在启动…")
        self.screen_detector = ScreenStateDetector(
            bundled_path("assets"),
            self._on_guard_state,
            lambda text: self.post_event("guard_status", text),
            self._on_guard_error,
            clear_frames=self.config.visual_clear_frames,
            is_guard_latched=lambda: self.engine.visual_guard_latched,
        )
        self.screen_detector.start()

    def _stop_screen_detector(self) -> None:
        detector = self.screen_detector
        self.screen_detector = None
        if detector is not None:
            detector.stop()
        self.engine.set_screen_blocked(False)
        self.guard_label.configure(text="界面检测：未启用")

    def _on_guard_state(
        self, blocked: bool, reason: str, mouse_score: float, clock_score: float
    ) -> None:
        self.engine.set_screen_blocked(blocked, reason)
        ignored_for_hold = (
            blocked
            and self.engine.physically_held
            and not self.engine.visual_guard_latched
        )
        self.post_event(
            "guard_state",
            blocked,
            reason,
            mouse_score,
            clock_score,
            ignored_for_hold,
        )

    def _on_guard_error(self, text: str) -> None:
        self.engine.set_screen_blocked(False)
        self.post_event("guard_error", text)

    def restart_as_admin(self) -> None:
        if getattr(sys, "frozen", False):
            executable = sys.executable
            parameters = None
        else:
            executable = sys.executable
            parameters = f'"{Path(__file__).resolve()}"'
        # Release the single-instance lock before starting the elevated replacement.
        if self.instance_guard:
            self.instance_guard.close()
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, parameters, str(Path.cwd()), 1
        )
        if result > 32:
            self.close()
        else:
            self.instance_guard = SingleInstanceGuard()
            messagebox.showerror("无法提升权限", "管理员启动被取消或被系统阻止。")

    def close(self) -> None:
        if self.screen_detector is not None:
            self.screen_detector.stop()
            self.screen_detector = None
        self.engine.shutdown()
        self.monitor.stop()
        self.sound_player.close()
        if self.instance_guard:
            self.instance_guard.close()
        self.root.destroy()


def main() -> None:
    if os.name != "nt":
        raise SystemExit("This application only supports Windows.")
    make_dpi_aware()
    instance_guard = SingleInstanceGuard()
    if instance_guard.already_running:
        ctypes.windll.user32.MessageBoxW(
            None,
            "自然长按连点器已经在运行。请关闭旧窗口后再启动新版。",
            APP_NAME,
            0x30,
        )
        instance_guard.close()
        return
    try:
        root = tk.Tk()
        App(root, instance_guard)
        root.mainloop()
    finally:
        instance_guard.close()


if __name__ == "__main__":
    main()
