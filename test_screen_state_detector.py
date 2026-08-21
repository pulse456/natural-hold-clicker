import os
import threading
import unittest
from pathlib import Path

import cv2

from screen_state_detector import (
    CLEAR_MARGIN,
    THRESH_CLOCK,
    THRESH_MOUSE,
    ScreenStateDetector,
    VisualStateMonitor,
    best_score,
    mouse_left_score,
)


ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "detector_test_samples"
ASSETS = ROOT / "assets"


def gray(path: Path):
    image = cv2.imread(os.fspath(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise AssertionError(f"无法读取测试图: {path}")
    return image


class RecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mouse_templates, cls.clock_template, cls.clock_mask = (
            VisualStateMonitor.__new__(VisualStateMonitor)._load_templates(ASSETS, 1.0)
        )

    def test_mouse_samples_have_a_clear_margin(self):
        positives = [
            mouse_left_score(gray(path), self.mouse_templates)
            for path in SAMPLES.glob("mouse_pos_*.png")
        ]
        negatives = [
            mouse_left_score(gray(path), self.mouse_templates)
            for path in SAMPLES.glob("mouse_neg_*.png")
        ]
        negatives.append(
            mouse_left_score(gray(SAMPLES / "mouse_right_click_only.png"), self.mouse_templates)
        )
        self.assertTrue(positives)
        self.assertTrue(negatives)
        self.assertGreaterEqual(min(positives), THRESH_MOUSE)
        self.assertLess(max(negatives), THRESH_MOUSE - CLEAR_MARGIN)

    def test_middle_mouse_prompt_cannot_trigger_left_icon(self):
        score = mouse_left_score(
            gray(SAMPLES / "mouse_ambiguous_middle_prompt_01.png"),
            self.mouse_templates,
        )
        self.assertLess(score, THRESH_MOUSE)

    def test_clock_samples_have_a_clear_margin(self):
        positives = [
            best_score(
                gray(path),
                self.clock_template,
                self.clock_mask,
                cv2.TM_CCOEFF_NORMED,
            )
            for path in SAMPLES.glob("clock_pos_*.png")
        ]
        negatives = [
            best_score(
                gray(path),
                self.clock_template,
                self.clock_mask,
                cv2.TM_CCOEFF_NORMED,
            )
            for path in SAMPLES.glob("clock_neg_*.png")
        ]
        self.assertGreaterEqual(min(positives), THRESH_CLOCK)
        self.assertLess(max(negatives), THRESH_CLOCK - CLEAR_MARGIN)


class DetectorTransitionTests(unittest.TestCase):
    def run_sequence(
        self, clear_frames, scores, is_guard_latched=None, detect_mouse=True
    ):
        transitions = []
        resumed = threading.Event()
        instances = []

        class FakeGrabber:
            name = "fake"

            @staticmethod
            def grab():
                return object()

        class FakeMonitor:
            def __init__(self, **_kwargs):
                self.grabber = FakeGrabber()
                self.scores = iter(scores)
                self.calls = 0
                instances.append(self)

            def check_capture(self, _frame):
                self.calls += 1
                try:
                    return next(self.scores)
                except StopIteration:
                    return THRESH_MOUSE + 0.1, 0.0

            @staticmethod
            def close():
                pass

        def on_state(blocked, reason, *_scores):
            transitions.append((blocked, reason, instances[0].calls))
            if not blocked:
                resumed.set()

        detector = ScreenStateDetector(
            ASSETS,
            on_state,
            lambda _text: None,
            lambda error: transitions.append(("error", error, 0)),
            interval=0.010,
            monitor_factory=FakeMonitor,
            clear_frames=clear_frames,
            is_guard_latched=is_guard_latched,
            detect_mouse=detect_mouse,
        )
        detector.start()
        try:
            self.assertTrue(resumed.wait(0.5))
        finally:
            detector.stop()
        return transitions

    def test_configured_number_of_clear_frames_is_required(self):
        for clear_frames in (1, 2, 3, 4):
            with self.subTest(clear_frames=clear_frames):
                scores = [(THRESH_MOUSE + 0.1, 0.0)] + [(0.0, 0.0)] * clear_frames
                transitions = self.run_sequence(clear_frames, scores)
                self.assertEqual(
                    transitions,
                    [
                        (True, "技能鼠标图标", 1),
                        (False, "", 1 + clear_frames),
                    ],
                )

    def test_uncertain_frame_resets_clear_streak(self):
        scores = [
            (THRESH_MOUSE + 0.1, 0.0),
            (0.0, 0.0),
            (THRESH_MOUSE - 0.04, 0.0),
            (0.0, 0.0),
            (0.0, 0.0),
            (0.0, 0.0),
        ]
        transitions = self.run_sequence(3, scores)
        self.assertEqual(transitions[-1], (False, "", 6))

    def test_clock_only_mode_ignores_mouse_matches(self):
        scores = [
            (THRESH_MOUSE + 0.1, THRESH_CLOCK + 0.1),
            (THRESH_MOUSE + 0.1, 0.0),
            (THRESH_MOUSE + 0.1, 0.0),
        ]
        transitions = self.run_sequence(2, scores, detect_mouse=False)
        self.assertEqual(
            transitions,
            [
                (True, "背包时钟", 1),
                (False, "", 3),
            ],
        )

    def test_guard_cannot_clear_while_engine_latch_is_active(self):
        latched = True

        def is_guard_latched():
            return latched

        transitions = []
        resumed = threading.Event()

        class FakeGrabber:
            name = "fake"

            @staticmethod
            def grab():
                return object()

        class FakeMonitor:
            def __init__(self, **_kwargs):
                self.grabber = FakeGrabber()
                self.calls = 0

            def check_capture(self, _frame):
                self.calls += 1
                return (THRESH_MOUSE + 0.1, 0.0) if self.calls == 1 else (0.0, 0.0)

            @staticmethod
            def close():
                pass

        def on_state(blocked, reason, *_scores):
            transitions.append((blocked, reason))
            if not blocked:
                resumed.set()

        detector = ScreenStateDetector(
            ASSETS,
            on_state,
            lambda _text: None,
            lambda error: transitions.append(("error", error)),
            interval=0.010,
            monitor_factory=FakeMonitor,
            clear_frames=2,
            is_guard_latched=is_guard_latched,
        )
        detector.start()
        try:
            self.assertTrue(detector._thread)
            self.assertFalse(resumed.wait(0.08))
            latched = False
            self.assertTrue(resumed.wait(0.15))
        finally:
            detector.stop()
        self.assertEqual(
            transitions,
            [(True, "技能鼠标图标"), (False, "")],
        )


if __name__ == "__main__":
    unittest.main()
