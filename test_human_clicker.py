import random
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from human_clicker import (
    AppConfig,
    ClickEngine,
    GlobalInputMonitor,
    HumanRhythm,
    SingleInstanceGuard,
    binding_conflict_message,
    input_code_label,
    normalized_pause_bindings,
)


class ConfigTests(unittest.TestCase):
    def test_validation_clamps_unsafe_values(self):
        config = AppConfig(
            clicks_per_minute=99999,
            jitter_percent=-2,
            hold_threshold_ms=0,
            visual_clear_frames=99,
            activation_mode="unknown",
            injection_mode="unknown",
        ).validated()
        self.assertEqual(config.clicks_per_minute, 3000)
        self.assertEqual(config.jitter_percent, 0)
        self.assertEqual(config.hold_threshold_ms, 30)
        self.assertEqual(config.visual_clear_frames, 10)
        self.assertEqual(config.activation_mode, "stable")
        self.assertEqual(config.injection_mode, "sendinput")

    def test_pause_bindings_are_deduplicated_and_clamped(self):
        bindings = normalized_pause_bindings(
            [
                ["k:81", 0, True],
                ["M:X1", 99999, True],
                ["K:81", 320, False],
                ["bad", 200, True],
            ]
        )
        self.assertEqual(bindings, (("K:81", 320, False), ("M:X1", 10000, True)))
        self.assertEqual(input_code_label("K:81"), "Q")
        self.assertEqual(input_code_label("M:X1"), "鼠标侧键 1")

    def test_reserved_and_trigger_keys_report_conflicts(self):
        self.assertIn(
            "暂停/继续",
            binding_conflict_message(
                AppConfig(toggle_hotkey="F8", pause_bindings=(("K:119", 200, True),))
            ),
        )
        self.assertIn(
            "连点触发键",
            binding_conflict_message(
                AppConfig(trigger_button="left", pause_bindings=(("M:LEFT", 200, True),))
            ),
        )


class HumanRhythmTests(unittest.TestCase):
    def test_zero_jitter_is_exact(self):
        rhythm = HumanRhythm(random.Random(1))
        self.assertEqual(rhythm.next_interval(600, 0, True), 0.1)

    def test_intervals_vary_and_average_near_target(self):
        rhythm = HumanRhythm(random.Random(42))
        values = [rhythm.next_interval(600, 12, True) for _ in range(20000)]
        self.assertGreater(max(values) - min(values), 0.02)
        self.assertAlmostEqual(sum(values) / len(values), 0.1, delta=0.004)

    def test_all_intervals_remain_positive_at_max_settings(self):
        rhythm = HumanRhythm(random.Random(7))
        values = [rhythm.next_interval(3000, 50, True) for _ in range(5000)]
        self.assertTrue(all(value >= 0.008 for value in values))

class ActivationModeTests(unittest.TestCase):
    def test_available_modes_never_suppress_native_click(self):
        for mode in ("stable", "progressive"):
            with self.subTest(mode=mode):
                engine = ClickEngine(
                    AppConfig(
                        activation_mode=mode,
                        hold_threshold_ms=30,
                        start_enabled=True,
                    ),
                    lambda *args: None,
                )
                self.assertFalse(engine.physical_down("left"))
                self.assertFalse(engine.physical_up("left"))
                engine._worker.join(timeout=0.5)

    def test_minimum_stable_threshold_starts_clicking(self):
        clicked = threading.Event()
        engine = ClickEngine(
            AppConfig(
                activation_mode="stable",
                hold_threshold_ms=30,
                clicks_per_minute=600,
                jitter_percent=0,
                natural_rhythm=False,
            ),
            lambda *args: None,
        )

        def send(*_args):
            clicked.set()
            return True

        with patch("human_clicker.send_button_event", side_effect=send):
            engine.physical_down("left")
            self.assertTrue(clicked.wait(0.15))
            engine.physical_up("left")
            engine._worker.join(timeout=0.5)


class ScreenGuardTests(unittest.TestCase):
    def test_blocking_is_silent_and_does_not_swallow_physical_input(self):
        events = []
        engine = ClickEngine(
            AppConfig(sound_enabled=True, start_enabled=True),
            lambda *args: events.append(args),
        )
        engine.set_screen_blocked(True, "背包时钟")
        with patch("human_clicker.send_button_event") as sender:
            self.assertFalse(engine.physical_down("left"))
            time.sleep(0.05)
            self.assertFalse(engine.physical_up("left"))
        sender.assert_not_called()
        engine.set_screen_blocked(False)
        self.assertFalse(any(event[0] == "sound" for event in events))

    def test_clearing_guard_restarts_held_press(self):
        clicked = threading.Event()
        engine = ClickEngine(
            AppConfig(
                hold_threshold_ms=30,
                clicks_per_minute=600,
                jitter_percent=0,
                natural_rhythm=False,
                start_enabled=True,
            ),
            lambda *args: None,
        )
        engine.set_screen_blocked(True, "技能鼠标图标")
        engine.physical_down("left")

        def send(*_args):
            clicked.set()
            return True

        with patch("human_clicker.send_button_event", side_effect=send):
            engine.set_screen_blocked(False)
            self.assertTrue(clicked.wait(0.15))
            engine.physical_up("left")
            engine._worker.join(timeout=0.5)


class KeyPauseTests(unittest.TestCase):
    def test_key_pause_is_silent_and_clears_automatically(self):
        events = []
        engine = ClickEngine(
            AppConfig(sound_enabled=True, start_enabled=True),
            lambda *args: events.append(args),
        )
        engine.trigger_key_pause("K:81", "Q", 40)
        blocked, reason = engine.auto_block
        self.assertTrue(blocked)
        self.assertIn("Q", reason)
        time.sleep(0.09)
        self.assertEqual(engine.auto_block, (False, ""))
        self.assertFalse(any(event[0] == "sound" for event in events))
        engine.shutdown()

    def test_visual_guard_keeps_clicker_blocked_after_key_timer_ends(self):
        engine = ClickEngine(AppConfig(start_enabled=True), lambda *args: None)
        engine.set_screen_blocked(True, "技能鼠标图标")
        engine.trigger_key_pause("K:69", "E", 30)
        time.sleep(0.07)
        blocked, reason = engine.auto_block
        self.assertTrue(blocked)
        self.assertIn("技能鼠标图标", reason)
        self.assertNotIn("按键预暂停", reason)
        engine.set_screen_blocked(False)
        self.assertEqual(engine.auto_block, (False, ""))
        engine.shutdown()

    def test_overlapping_key_windows_clear_independently(self):
        engine = ClickEngine(AppConfig(start_enabled=True), lambda *args: None)
        engine.trigger_key_pause("K:81", "Q", 35)
        time.sleep(0.015)
        engine.trigger_key_pause("K:69", "E", 80)
        time.sleep(0.045)
        blocked, reason = engine.auto_block
        self.assertTrue(blocked)
        self.assertNotIn("Q", reason)
        self.assertIn("E", reason)
        time.sleep(0.06)
        self.assertEqual(engine.auto_block, (False, ""))
        engine.shutdown()

    def test_monitor_ignores_key_repeat_until_release(self):
        engine = ClickEngine(
            AppConfig(pause_bindings=(("K:81", 200, True),)),
            lambda *args: None,
        )
        monitor = GlobalInputMonitor(engine, lambda *args: None)
        with patch.object(engine, "trigger_key_pause") as trigger:
            monitor._handle_input_event("K:81", "Q", True)
            monitor._handle_input_event("K:81", "Q", True)
            monitor._handle_input_event("K:81", "Q", False)
            monitor._handle_input_event("K:81", "Q", True)
        self.assertEqual(trigger.call_count, 2)
        engine.shutdown()

    def test_binding_capture_swallows_down_and_matching_up(self):
        events = []
        engine = ClickEngine(AppConfig(), lambda *args: None)
        monitor = GlobalInputMonitor(engine, lambda *args: events.append(args))
        monitor.begin_binding_capture()
        self.assertTrue(monitor._handle_input_event("M:X2", "鼠标侧键 2", True))
        self.assertTrue(monitor._handle_input_event("M:X2", "鼠标侧键 2", False))
        self.assertEqual(events, [("binding_captured", "M:X2", "鼠标侧键 2")])
        engine.shutdown()


class SoundNotificationTests(unittest.TestCase):
    def test_toggle_emits_distinct_sound_notifications(self):
        events = []
        engine = ClickEngine(AppConfig(sound_enabled=True, start_enabled=True), lambda *args: events.append(args))
        engine.set_enabled(False, "hotkey")
        engine.set_enabled(True, "hotkey")
        engine.panic()
        tones = [event[1] for event in events if event[0] == "sound"]
        self.assertEqual(tones, ["disabled", "enabled", "panic"])

    def test_sound_can_be_disabled(self):
        events = []
        engine = ClickEngine(AppConfig(sound_enabled=False, start_enabled=True), lambda *args: events.append(args))
        engine.set_enabled(False, "hotkey")
        self.assertFalse(any(event[0] == "sound" for event in events))


class SingleInstanceTests(unittest.TestCase):
    def test_second_instance_is_detected(self):
        mutex_name = f"Local\\NaturalHoldClicker.Test.{uuid.uuid4()}"
        first = SingleInstanceGuard(mutex_name)
        second = SingleInstanceGuard(mutex_name)
        try:
            self.assertFalse(first.already_running)
            self.assertTrue(second.already_running)
        finally:
            second.close()
            first.close()


if __name__ == "__main__":
    unittest.main()
