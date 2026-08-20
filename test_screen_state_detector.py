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
            for path in SAMPLES.glob("mouse_pos_previous_*.png")
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
    def test_one_positive_frame_blocks_and_two_clear_frames_resume(self):
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
                self.scores = iter(
                    [
                        (THRESH_MOUSE + 0.1, 0.0),
                        (0.0, 0.0),
                        (0.0, 0.0),
                    ]
                )

            def check_capture(self, _frame):
                try:
                    return next(self.scores)
                except StopIteration:
                    return 0.0, 0.0

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
            lambda error: self.fail(error),
            interval=0.010,
            monitor_factory=FakeMonitor,
        )
        detector.start()
        try:
            self.assertTrue(resumed.wait(0.5))
        finally:
            detector.stop()

        self.assertEqual(
            transitions,
            [(True, "技能鼠标图标"), (False, "")],
        )


if __name__ == "__main__":
    unittest.main()
