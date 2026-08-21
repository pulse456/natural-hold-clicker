from __future__ import annotations

import ctypes
import os
import threading
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import ImageGrab


REF_W, REF_H = 1920, 1080
REGION_MOUSE = (1864, 500, 38, 260)
REGION_CLOCK = (1806, 15, 41, 46)
THRESH_MOUSE = 0.70
THRESH_CLOCK = 0.50
CLEAR_MARGIN = 0.08
DEFAULT_CLEAR_FRAMES = 3
CHECK_INTERVAL = 0.025

MOUSE_TEMPLATE_FILES = (
    "template_mouse_left_3.png",
    "template_mouse_left_4.png",
    "template_mouse_left_5.png",
    "template_mouse_left_6.png",
)


def make_dpi_aware() -> None:
    """Keep capture coordinates aligned with physical pixels on scaled displays."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def scale_region(region: tuple[int, int, int, int], scale: float) -> tuple[int, int, int, int]:
    x, y, width, height = region
    x1, y1 = round(x * scale), round(y * scale)
    x2, y2 = round((x + width) * scale), round((y + height) * scale)
    return x1, y1, max(1, x2 - x1), max(1, y2 - y1)


def offset_region(region: tuple[int, int, int, int], ox: int, oy: int) -> tuple[int, int, int, int]:
    x, y, width, height = region
    return x + ox, y + oy, width, height


def enclosing_region(*regions: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    left = min(region[0] for region in regions)
    top = min(region[1] for region in regions)
    right = max(region[0] + region[2] for region in regions)
    bottom = max(region[1] + region[3] for region in regions)
    return left, top, right - left, bottom - top


def crop_region(frame: np.ndarray, region: tuple[int, int, int, int], label: str) -> np.ndarray:
    x, y, width, height = region
    frame_height, frame_width = frame.shape[:2]
    if x < 0 or y < 0 or x + width > frame_width or y + height > frame_height:
        raise ValueError(
            f"{label}超出截屏范围: region={region}, frame={frame_width}x{frame_height}"
        )
    return frame[y : y + height, x : x + width]


def gradient_map(gray: np.ndarray) -> np.ndarray:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.convertScaleAbs(cv2.magnitude(gx, gy))


def best_score(roi: np.ndarray, template: np.ndarray, mask: np.ndarray | None, method: int) -> float:
    if roi.shape[0] < template.shape[0] or roi.shape[1] < template.shape[1]:
        return 0.0
    result = cv2.matchTemplate(roi, template, method, mask=mask)
    result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
    _, maximum, _, _ = cv2.minMaxLoc(result)
    return float(maximum)


def mouse_left_score(gray_roi: np.ndarray, templates: list[np.ndarray]) -> float:
    edges = gradient_map(gray_roi)
    return max(
        (
            best_score(edges, template, None, cv2.TM_CCOEFF_NORMED)
            for template in templates
        ),
        default=0.0,
    )


class ScreenGrabber:
    """Persistent capture backend with DXCam -> MSS -> Pillow fallback."""

    def __init__(self, region: tuple[int, int, int, int], backend: str = "auto") -> None:
        if backend not in {"auto", "dxcam", "mss", "pillow"}:
            raise ValueError(f"未知截屏后端: {backend}")
        self.region = region
        self.name = ""
        self._resource = None
        self._monitor = None
        candidates = ("dxcam", "mss", "pillow") if backend == "auto" else (backend,)
        errors: list[str] = []
        for candidate in candidates:
            try:
                self._open(candidate)
                self.name = candidate
                return
            except Exception as exc:
                self.close()
                errors.append(f"{candidate}: {exc}")
        raise RuntimeError("无法初始化截屏后端；" + "；".join(errors))

    def _open(self, backend: str) -> None:
        x, y, width, height = self.region
        if backend == "dxcam":
            if x < 0 or y < 0:
                raise RuntimeError("DXCam不支持负坐标区域")
            import dxcam

            self._resource = dxcam.create(
                region=(x, y, x + width, y + height),
                output_color="BGR",
                max_buffer_len=2,
            )
            frame = self._resource.grab(copy=False, new_frame_only=False)
            if frame is None or frame.shape[:2] != (height, width):
                raise RuntimeError("DXCam未返回有效画面")
        elif backend == "mss":
            import mss

            self._resource = mss.MSS()
            self._monitor = {"left": x, "top": y, "width": width, "height": height}
            frame = np.asarray(self._resource.grab(self._monitor))
            if frame.shape[:2] != (height, width):
                raise RuntimeError("MSS未返回有效画面")
        else:
            image = ImageGrab.grab(
                bbox=(x, y, x + width, y + height), all_screens=True
            )
            if image.size != (width, height):
                raise RuntimeError("Pillow未返回有效画面")

    def grab(self) -> np.ndarray:
        x, y, width, height = self.region
        if self.name == "dxcam":
            frame = self._resource.grab(copy=False, new_frame_only=False)
            if frame is None:
                raise RuntimeError("DXCam返回空帧")
            return frame
        if self.name == "mss":
            return np.asarray(self._resource.grab(self._monitor))[:, :, :3]
        image = ImageGrab.grab(
            bbox=(x, y, x + width, y + height), all_screens=True
        )
        return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)

    def close(self) -> None:
        if self._resource is not None:
            try:
                if hasattr(self._resource, "release"):
                    self._resource.release()
                elif hasattr(self._resource, "close"):
                    self._resource.close()
            except Exception:
                pass
        self._resource = None
        self._monitor = None


class VisualStateMonitor:
    def __init__(
        self,
        assets_dir: Path,
        screen_rect: tuple[int, int, int, int] | None = None,
        capture_backend: str = "auto",
        detect_mouse: bool = True,
    ) -> None:
        if screen_rect is None:
            user32 = ctypes.windll.user32
            screen_rect = (
                0,
                0,
                user32.GetSystemMetrics(0),
                user32.GetSystemMetrics(1),
            )
        self.ox, self.oy, width, height = screen_rect
        if width <= 0 or height <= 0:
            raise ValueError(f"无效屏幕尺寸: {width}x{height}")
        scale = width / REF_W
        if abs(height / REF_H - scale) > 0.02:
            raise ValueError(f"当前画面不是受支持的16:9比例: {width}x{height}")

        self.detect_mouse = bool(detect_mouse)
        self.mouse_templates, self.clock_template, self.clock_mask = self._load_templates(
            Path(assets_dir), scale
        )
        client_mouse = scale_region(REGION_MOUSE, scale)
        client_clock = scale_region(REGION_CLOCK, scale)
        self.screen_mouse = offset_region(client_mouse, self.ox, self.oy)
        self.screen_clock = offset_region(client_clock, self.ox, self.oy)
        self.capture_region = (
            enclosing_region(self.screen_mouse, self.screen_clock)
            if self.detect_mouse
            else self.screen_clock
        )
        self.grabber = ScreenGrabber(self.capture_region, capture_backend)

    @staticmethod
    def _read_gray(path: Path) -> np.ndarray:
        image = cv2.imread(os.fspath(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise RuntimeError(f"无法读取检测模板: {path.name}")
        return image

    def _load_templates(
        self, assets_dir: Path, scale: float
    ) -> tuple[list[np.ndarray], np.ndarray, np.ndarray]:
        mouse_raw = [
            self._read_gray(assets_dir / filename) for filename in MOUSE_TEMPLATE_FILES
        ]
        clock = self._read_gray(assets_dir / "template_bell.png")
        clock_mask = self._read_gray(assets_dir / "template_bell_mask.png")
        if clock.shape != clock_mask.shape or not np.any(clock_mask):
            raise RuntimeError("时钟模板或掩码无效")
        if abs(scale - 1.0) > 1e-6:
            mouse_raw = [
                cv2.resize(
                    image,
                    None,
                    fx=scale,
                    fy=scale,
                    interpolation=cv2.INTER_AREA,
                )
                for image in mouse_raw
            ]
            clock = cv2.resize(
                clock, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
            )
            clock_mask = cv2.resize(
                clock_mask,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_NEAREST,
            )
            clock_mask = ((clock_mask > 127) * 255).astype(np.uint8)
        return [gradient_map(image) for image in mouse_raw], clock, clock_mask

    def check_capture(self, captured: np.ndarray) -> tuple[float, float]:
        capture_x, capture_y, _, _ = self.capture_region
        clock_local = offset_region(self.screen_clock, -capture_x, -capture_y)
        clock_roi = crop_region(captured, clock_local, "时钟区域")
        mouse_score = 0.0
        if self.detect_mouse:
            mouse_local = offset_region(self.screen_mouse, -capture_x, -capture_y)
            mouse_roi = crop_region(captured, mouse_local, "鼠标左键区域")
            mouse_score = mouse_left_score(
                cv2.cvtColor(mouse_roi, cv2.COLOR_BGR2GRAY), self.mouse_templates
            )
        clock_score = best_score(
            cv2.cvtColor(clock_roi, cv2.COLOR_BGR2GRAY),
            self.clock_template,
            self.clock_mask,
            cv2.TM_CCOEFF_NORMED,
        )
        return mouse_score, clock_score

    def close(self) -> None:
        self.grabber.close()


class ScreenStateDetector:
    """Silent detector that reports whether automatic clicking should be blocked."""

    def __init__(
        self,
        assets_dir: Path,
        on_state: Callable[[bool, str, float, float], None],
        on_status: Callable[[str], None],
        on_error: Callable[[str], None],
        interval: float = CHECK_INTERVAL,
        capture_backend: str = "auto",
        monitor_factory: Callable[..., VisualStateMonitor] = VisualStateMonitor,
        clear_frames: int = DEFAULT_CLEAR_FRAMES,
        is_guard_latched: Callable[[], bool] | None = None,
        detect_mouse: bool = True,
    ) -> None:
        self.assets_dir = Path(assets_dir)
        self.on_state = on_state
        self.on_status = on_status
        self.on_error = on_error
        self.interval = max(0.010, float(interval))
        self.capture_backend = capture_backend
        self.monitor_factory = monitor_factory
        self.clear_frames = max(1, min(10, int(clear_frames)))
        self.is_guard_latched = is_guard_latched or (lambda: False)
        self.detect_mouse = bool(detect_mouse)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None

    def set_clear_frames(self, clear_frames: int) -> None:
        self.clear_frames = max(1, min(10, int(clear_frames)))

    def _run(self) -> None:
        monitor: VisualStateMonitor | None = None
        blocked = False
        blocked_reason = ""
        clear_frames = 0
        try:
            monitor = self.monitor_factory(
                assets_dir=self.assets_dir,
                capture_backend=self.capture_backend,
                detect_mouse=self.detect_mouse,
            )
            scope = "背包时钟＋技能左键" if self.detect_mouse else "仅背包时钟"
            self.on_status(
                f"监控中 · {scope} · {monitor.grabber.name.upper()} · "
                f"{round(1 / self.interval)} Hz"
            )
            while not self._stop.is_set():
                started = time.perf_counter()
                frame = monitor.grabber.grab()
                mouse_score, clock_score = monitor.check_capture(frame)
                mouse_found = self.detect_mouse and mouse_score >= THRESH_MOUSE
                clock_found = clock_score >= THRESH_CLOCK

                if mouse_found or clock_found:
                    clear_frames = 0
                    reasons = []
                    if clock_found:
                        reasons.append("背包时钟")
                    if mouse_found:
                        reasons.append("技能鼠标图标")
                    reason = "、".join(reasons)
                    if not blocked or reason != blocked_reason:
                        blocked = True
                        blocked_reason = reason
                        self.on_state(True, reason, mouse_score, clock_score)
                elif blocked:
                    # The engine decides the visual policy once per physical press.
                    # Only a press that began guarded keeps detector recovery latched;
                    # a press that began clear may continue updating raw detector state
                    # without changing that press's effective clicking policy.
                    if self.is_guard_latched():
                        clear_frames = 0
                    else:
                        clearly_absent = (
                            (
                                not self.detect_mouse
                                or mouse_score < THRESH_MOUSE - CLEAR_MARGIN
                            )
                            and clock_score < THRESH_CLOCK - CLEAR_MARGIN
                        )
                        clear_frames = clear_frames + 1 if clearly_absent else 0
                        if clear_frames >= self.clear_frames:
                            blocked = False
                            blocked_reason = ""
                            clear_frames = 0
                            self.on_state(False, "", mouse_score, clock_score)

                elapsed = time.perf_counter() - started
                self._stop.wait(max(0.0, self.interval - elapsed))
        except Exception as exc:
            if blocked:
                self.on_state(False, "", 0.0, 0.0)
            if not self._stop.is_set():
                self.on_error(str(exc))
        finally:
            if monitor is not None:
                monitor.close()
