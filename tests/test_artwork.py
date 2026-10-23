import subprocess
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import main


class TestArtwork(unittest.TestCase):
    def test_valid_cover_returns_jpeg(self):
        cover = "imagesw/airplay-covers/cover-abc_123.jpg"
        result = subprocess.CompletedProcess(
            args=["ssh"], returncode=0, stdout=b"jpeg-bytes",
        )
        with patch("main.airplay", return_value={
            "connected": True, "cover_url": cover,
        }), patch("main.run_pi_command", return_value=result) as command:
            response = main.artwork()

        self.assertEqual(response.body, b"jpeg-bytes")
        self.assertEqual(response.media_type, "image/jpeg")
        self.assertEqual(response.headers["cache-control"], "private, max-age=60")
        command.assert_called_once_with(f"cat /var/local/www/{cover}", text=False)

    def test_unavailable_metadata_returns_503(self):
        with patch("main.airplay", return_value={"connected": False}), patch(
            "main.run_pi_command"
        ) as command:
            with self.assertRaises(HTTPException) as error:
                main.artwork()
        self.assertEqual(error.exception.status_code, 503)
        command.assert_not_called()

    def test_missing_cover_returns_404(self):
        with patch("main.airplay", return_value={"connected": True}), patch(
            "main.run_pi_command"
        ) as command:
            with self.assertRaises(HTTPException) as error:
                main.artwork()
        self.assertEqual(error.exception.status_code, 404)
        command.assert_not_called()

    def test_unsafe_cover_paths_are_rejected_before_ssh(self):
        for cover in ("../../etc/passwd", "imagesw/airplay-covers/cover-a.jpg;id",
                      "imagesw/airplay-covers/cover-$(id).jpg", "/etc/passwd"):
            with self.subTest(cover=cover):
                with patch("main.airplay", return_value={
                    "connected": True, "cover_url": cover,
                }), patch("main.run_pi_command") as command:
                    with self.assertRaises(HTTPException) as error:
                        main.artwork()
                self.assertEqual(error.exception.status_code, 400)
                command.assert_not_called()

    def test_failed_artwork_read_returns_502(self):
        with patch("main.airplay", return_value={
            "connected": True,
            "cover_url": "imagesw/airplay-covers/cover-abc.jpg",
        }), patch("main.run_pi_command", side_effect=
                  subprocess.TimeoutExpired(cmd="ssh", timeout=5)):
            with self.assertRaises(HTTPException) as error:
                main.artwork()
        self.assertEqual(error.exception.status_code, 502)
