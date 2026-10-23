import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


CARDS = """ 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
 2 [K11            ]: USB-Audio - FiiO K11
                      FIIO FiiO K11 at usb-0000:01:00.0-1.1, high speed
"""
OPEN = "access: MMAP_INTERLEAVED\nformat: S32_LE\nchannels: 2\nrate: 48000 (48000/1)\n"


class TestOutputDetection(unittest.TestCase):
    def detect(self, streams, cards=CARDS):
        # Execute the actual remote scanner against a temporary /proc fixture.
        # No SSH connection or real audio device is used.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cards").write_text(cards)
            for name, content in streams.items():
                path = root / name / "hw_params"
                path.parent.mkdir(parents=True)
                path.write_text(content)

            def run_fixture(command):
                self.assertTrue(command.startswith("python3 - <<'PY'\n"))
                script = command.split("\n", 1)[1].rsplit("\nPY", 1)[0]
                script = script.replace("/proc/asound", tmp)
                return subprocess.run(
                    [sys.executable, "-c", script],
                    capture_output=True, text=True, check=True,
                )

            with patch("main.run_pi_command", side_effect=run_fixture):
                return main.read_output_devices()

    def test_detects_dac_without_assuming_card_zero(self):
        self.assertEqual(self.detect({
            "card0/pcm0p/sub0": "closed\n",
            "card2/pcm0p/sub0": OPEN,
        }), ["FiiO K11"])

    def test_closed_outputs_are_idle(self):
        self.assertEqual(self.detect({"card2/pcm0p/sub0": "closed\n"}), [])

    def test_multiple_outputs_are_listed_and_subdevices_deduplicated(self):
        self.assertEqual(self.detect({
            "card0/pcm0p/sub0": OPEN,
            "card2/pcm0p/sub0": OPEN,
            "card2/pcm0p/sub1": OPEN,
        }), ["vc4-hdmi-0", "FiiO K11"])

    def test_no_cards_or_streams(self):
        self.assertEqual(self.detect({}, cards="--- no soundcards ---\n"), [])

    def test_invalid_scanner_response_is_rejected(self):
        for output in ('null', '{}', '[12]', '[""]', 'broken'):
            with self.subTest(output=output):
                result = subprocess.CompletedProcess([], 0, stdout=output)
                with patch("main.run_pi_command", return_value=result):
                    with self.assertRaises(ValueError):
                        main.read_output_devices()


class TestOutputEndpoint(unittest.TestCase):
    def setUp(self):
        original = main.output_cache.copy()
        main.output_cache.update(data=None, timestamp=0)
        self.addCleanup(main.output_cache.update, original)

    def test_active_output(self):
        with patch("main.read_output_devices", return_value=["FiiO K11"]):
            self.assertEqual(main.output_device(), {
                "state": "active", "devices": ["FiiO K11"],
            })

    def test_idle_output(self):
        with patch("main.read_output_devices", return_value=[]):
            self.assertEqual(main.output_device(), {"state": "idle", "devices": []})

    def test_read_failure_is_unavailable_and_retried(self):
        with patch("main.read_output_devices", side_effect=[
            subprocess.TimeoutExpired(cmd="ssh", timeout=5), ["FiiO K11"],
        ]), self.assertLogs("apple-music-bridge", level="WARNING"):
            self.assertEqual(main.output_device(), {
                "state": "unavailable", "devices": [],
            })
            self.assertEqual(main.output_device()["devices"], ["FiiO K11"])

    def test_cached_output_refreshes_after_expiry(self):
        with patch("main.time.monotonic") as clock, patch(
            "main.read_output_devices", side_effect=[["FiiO K11"], []]
        ) as read:
            clock.return_value = 100
            self.assertEqual(main.output_device()["devices"], ["FiiO K11"])
            clock.return_value = 104
            self.assertEqual(main.output_device()["devices"], ["FiiO K11"])
            clock.return_value = 105
            self.assertEqual(main.output_device()["state"], "idle")
        self.assertEqual(read.call_count, 2)
