import random
import unittest
import uuid
from unittest.mock import patch

from human_clicker import AppConfig, ClickEngine, HumanRhythm, SingleInstanceGuard


class ConfigTests(unittest.TestCase):
    def test_validation_clamps_unsafe_values(self):
        config = AppConfig(
            clicks_per_minute=99999,
            jitter_percent=-2,
            hold_threshold_ms=10,
            activation_mode="unknown",
            injection_mode="unknown",
        ).validated()
        self.assertEqual(config.clicks_per_minute, 3000)
        self.assertEqual(config.jitter_percent, 0)
        self.assertEqual(config.hold_threshold_ms, 60)
        self.assertEqual(config.activation_mode, "stable")
        self.assertEqual(config.injection_mode, "sendinput")


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

    def test_fast_tap_duration_is_short_and_positive(self):
        rhythm = HumanRhythm(random.Random(11))
        values = [rhythm.fast_tap_hold_time(0.1, True) for _ in range(1000)]
        self.assertTrue(all(0.014 <= value <= 0.040 for value in values))


class ActivationModeTests(unittest.TestCase):
    def test_tap_all_suppresses_physical_pair_and_generates_one_tap(self):
        events = []
        engine = ClickEngine(
            AppConfig(
                activation_mode="tap_all",
                injection_mode="sendinput",
                clicks_per_minute=600,
                jitter_percent=0,
                natural_rhythm=False,
                start_enabled=True,
            ),
            lambda *args: events.append(args),
        )
        with patch("human_clicker.send_button_event", return_value=True) as sender:
            self.assertTrue(engine.physical_down("left"))
            self.assertTrue(engine.physical_up("left"))
            engine._worker.join(timeout=0.5)
            self.assertFalse(engine._worker.is_alive())
        self.assertGreaterEqual(sender.call_count, 2)
        generated = [event for event in events if event[0] == "stats" and event[2] == 1]
        self.assertTrue(generated)

    def test_tap_all_passes_input_through_while_paused(self):
        engine = ClickEngine(
            AppConfig(activation_mode="tap_all", start_enabled=False), lambda *args: None
        )
        with patch("human_clicker.send_button_event") as sender:
            self.assertFalse(engine.physical_down("left"))
            self.assertFalse(engine.physical_up("left"))
        sender.assert_not_called()

    def test_non_replacement_modes_never_suppress_native_click(self):
        for mode in ("stable", "progressive", "rapid"):
            with self.subTest(mode=mode):
                engine = ClickEngine(
                    AppConfig(
                        activation_mode=mode,
                        hold_threshold_ms=60,
                        start_enabled=True,
                    ),
                    lambda *args: None,
                )
                self.assertFalse(engine.physical_down("left"))
                self.assertFalse(engine.physical_up("left"))
                engine._worker.join(timeout=0.5)


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
