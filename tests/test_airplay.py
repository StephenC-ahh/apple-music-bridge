import json
import subprocess
import unittest
from unittest.mock import patch

import main


class TestAirPlayMetadata(unittest.TestCase):
    def setUp(self):
        # Give each test a fresh cache and restore it afterwards.
        original_cache = main.airplay_cache.copy()
        main.airplay_cache.update(data=None, timestamp=0)

        self.addCleanup(main.airplay_cache.update, original_cache)

    def read_metadata(self, metadata):
        result = subprocess.CompletedProcess(
            args=["ssh"],
            returncode=0,
            stdout=json.dumps(metadata),
            stderr="",
        )

        with patch("main.run_pi_command", return_value=result):
            return main.airplay()

    def test_playing_track(self):
        player = self.read_metadata({
            "playstate": "Resume",
            "title": "Rough Boy",
            "artist": "ZZ Top",
            "album": "Goin' 50",
            "duration": "290640",
            "sformat": "AAC 24/48 kHz 2ch",
            "oformat": "PCM 32/48 kHz, 2ch",
        })

        self.assertTrue(player["connected"])
        self.assertEqual(player["state"], "playing")
        self.assertEqual(player["title"], "Rough Boy")
        self.assertEqual(player["artist"], "ZZ Top")
        self.assertEqual(player["duration_ms"], "290640")
        self.assertEqual(player["source_format"], "AAC 24/48 kHz 2ch")
        self.assertEqual(player["output_format"], "PCM 32/48 kHz, 2ch")

    def test_paused_track(self):
        player = self.read_metadata({"playstate": "Pause"})

        self.assertTrue(player["connected"])
        self.assertEqual(player["state"], "paused")

    def test_unknown_playback_event(self):
        player = self.read_metadata({"playstate": "Unexpected"})

        self.assertEqual(player["state"], "unknown")

    def test_empty_metadata_is_disconnected(self):
        result = subprocess.CompletedProcess(
            args=["ssh"],
            returncode=0,
            stdout="",
            stderr="",
        )

        with patch("main.run_pi_command", return_value=result):
            player = main.airplay()

        self.assertFalse(player["connected"])
        self.assertEqual(player["state"], "disconnected")

    def test_invalid_json_is_unavailable(self):
        result = subprocess.CompletedProcess(
            args=["ssh"],
            returncode=0,
            stdout="{broken json",
            stderr="",
        )

        with patch("main.run_pi_command", return_value=result):
            player = main.airplay()

        self.assertFalse(player["connected"])
        self.assertEqual(player["state"], "unavailable")

    def test_ssh_timeout_is_unavailable(self):
        with patch(
            "main.run_pi_command",
            side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=5),
        ):
            player = main.airplay()

        self.assertFalse(player["connected"])
        self.assertEqual(player["state"], "unavailable")
        self.assertIn("error", player)


if __name__ == "__main__":
    unittest.main()