import subprocess
import unittest
from unittest.mock import patch

import main


class TestAirPlayProgress(unittest.TestCase):
    def read_progress(self, timing, **metadata):
        player = {"connected": True, "state": "playing", "duration_ms": "10000"}
        player.update(metadata)
        with patch("main.read_airplay_timing", return_value=timing), patch(
            "main.airplay", return_value=player
        ):
            return main.progress()

    def test_halfway_through_track(self):
        result = self.read_progress((100, 600, 1100))
        self.assertTrue(result["available"])
        self.assertEqual(result["elapsed_ms"], 5000)
        self.assertEqual(result["duration_ms"], 10000)
        self.assertEqual(result["percent"], 50.0)

    def test_start_and_end_of_track(self):
        for current, percent in ((100, 0.0), (1100, 100.0)):
            with self.subTest(current=current):
                result = self.read_progress((100, current, 1100))
                self.assertTrue(result["available"])
                self.assertEqual(result["percent"], percent)

    def test_wrapping_frame_counter(self):
        result = self.read_progress((2**32 - 500, 0, 500))
        self.assertTrue(result["available"])
        self.assertEqual(result["elapsed_ms"], 5000)

    def test_paused_state_is_preserved(self):
        result = self.read_progress((100, 600, 1100), state="paused")
        self.assertTrue(result["available"])
        self.assertEqual(result["state"], "paused")

    def test_invalid_frame_ranges_are_unavailable(self):
        for timing in ((100, 100, 100), (100, 1200, 1100), (100, 50, 1100)):
            with self.subTest(timing=timing):
                self.assertFalse(self.read_progress(timing)["available"])

    def test_missing_or_invalid_duration_is_unavailable(self):
        for duration in (None, "0", "-1", "banana"):
            with self.subTest(duration=duration):
                result = self.read_progress((100, 600, 1100), duration_ms=duration)
                self.assertFalse(result["available"])

    def test_disconnected_or_unknown_state_is_unavailable(self):
        for metadata in ({"connected": False}, {"state": "unknown"}):
            with self.subTest(metadata=metadata):
                self.assertFalse(
                    self.read_progress((100, 600, 1100), **metadata)["available"]
                )

    def test_timing_failure_is_unavailable(self):
        with patch("main.read_airplay_timing", side_effect=
                   subprocess.TimeoutExpired(cmd="ssh", timeout=5)):
            self.assertFalse(main.progress()["available"])


class TestTimingParsing(unittest.TestCase):
    def test_valid_dbus_timing(self):
        result = subprocess.CompletedProcess(
            args=["ssh"], returncode=0, stdout='s "100/200/1100"\ns "600/12345"\n',
        )
        with patch("main.run_pi_command", return_value=result):
            self.assertEqual(main.read_airplay_timing(), (100, 600, 1100))

    def test_missing_or_malformed_dbus_timing(self):
        for output in ("", 's "100/200"\ns "600/12345"',
                       's "100/200/1100"\ns "bad/12345"'):
            with self.subTest(output=output):
                result = subprocess.CompletedProcess(
                    args=["ssh"], returncode=0, stdout=output,
                )
                with patch("main.run_pi_command", return_value=result):
                    with self.assertRaises(ValueError):
                        main.read_airplay_timing()
