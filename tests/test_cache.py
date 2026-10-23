import json
import subprocess
import unittest
from unittest.mock import patch

import main


class TestAirPlayCache(unittest.TestCase):
    def setUp(self):
        original = main.airplay_cache.copy()
        main.airplay_cache.update(data=None, timestamp=0)
        self.addCleanup(main.airplay_cache.update, original)

    def metadata_result(self, title):
        return subprocess.CompletedProcess(
            args=["ssh"], returncode=0,
            stdout=json.dumps({"title": title, "playstate": "Resume"}),
            stderr="",
        )

    def test_cached_metadata_avoids_another_ssh_call(self):
        with patch("main.time.monotonic", return_value=100), patch(
            "main.run_pi_command", return_value=self.metadata_result("First")
        ) as command:
            first = main.airplay()
            second = main.airplay()

        self.assertEqual(second["title"], first["title"])
        command.assert_called_once()

    def test_cache_refreshes_at_expiry(self):
        with patch("main.time.monotonic") as clock, patch(
            "main.run_pi_command",
            side_effect=[self.metadata_result("First"), self.metadata_result("Next")],
        ) as command:
            clock.return_value = 100
            self.assertEqual(main.airplay()["title"], "First")
            clock.return_value = 100 + main.AIRPLAY_CACHE_TTL - 0.01
            self.assertEqual(main.airplay()["title"], "First")
            clock.return_value = 100 + main.AIRPLAY_CACHE_TTL
            self.assertEqual(main.airplay()["title"], "Next")

        self.assertEqual(command.call_count, 2)

    def test_failed_read_is_retried(self):
        with patch("main.run_pi_command", side_effect=[
            subprocess.TimeoutExpired(cmd="ssh", timeout=5),
            self.metadata_result("Recovered"),
        ]) as command:
            self.assertEqual(main.airplay()["state"], "unavailable")
            self.assertEqual(main.airplay()["title"], "Recovered")

        self.assertEqual(command.call_count, 2)
