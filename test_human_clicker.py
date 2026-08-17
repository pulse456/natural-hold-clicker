import random
import unittest

from human_clicker import AppConfig, HumanRhythm


class ConfigTests(unittest.TestCase):
    def test_validation_clamps_unsafe_values(self):
        config = AppConfig(clicks_per_minute=99999, jitter_percent=-2, hold_threshold_ms=10).validated()
        self.assertEqual(config.clicks_per_minute, 3000)
        self.assertEqual(config.jitter_percent, 0)
        self.assertEqual(config.hold_threshold_ms, 150)


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


if __name__ == "__main__":
    unittest.main()
