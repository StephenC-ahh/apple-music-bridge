import os
from pathlib import Path
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestPortConfiguration(unittest.TestCase):
    def run_with_port(self, value):
        env = os.environ.copy()

        if value is None:
            env.pop("MPD_PORT", None)
        else:
            env["MPD_PORT"] = value

        return subprocess.run(
            [
                sys.executable,
                "-c",
                "import main; print(main.MPD_PORT)",
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_default_port(self):
        result = self.run_with_port(None)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "6600")

    def test_valid_ports(self):
        for port in ("1", "6601", "65535"):
            with self.subTest(port=port):
                result = self.run_with_port(port)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), port)

    def test_invalid_ports(self):
        for port in ("banana", "", "0", "-1", "65536"):
            with self.subTest(port=port):
                result = self.run_with_port(port)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "ValueError: MPD_PORT must be",
                    result.stderr,
                )


if __name__ == "__main__":
    unittest.main()