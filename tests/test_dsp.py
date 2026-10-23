import json
import math
import unittest
from threading import Event, Thread
from unittest.mock import MagicMock, patch

from websockets.sync.server import serve

import dsp
import main


def replies(state="Running"):
    return {
        "GetState": state,
        "GetSignalLevelsSinceLast": {
            "playback_rms": [-24, -18], "playback_peak": [-12, -6],
            "capture_rms": [-20, -14], "capture_peak": [-8, -2],
        },
        "GetClippedSamples": 0,
        "GetProcessingLoad": 0.8,
    }


def fake_connection(values):
    connection = MagicMock()
    def respond(timeout):
        command = json.loads(connection.send.call_args.args[0])
        return json.dumps({command: {"result": "Ok", "value": values[command]}})
    connection.recv.side_effect = respond
    return connection


class TestDspProtocol(unittest.TestCase):
    def test_running_sample_and_read_only_commands(self):
        connection = fake_connection(replies())
        result = dsp.read_sample(connection)
        self.assertEqual(result["output_rms_db"], [-24, -18])
        self.assertEqual(result["output_peak_db"], [-12, -6])
        self.assertEqual(result["clipped_samples"], 0)
        self.assertEqual(result["processing_load"], 0.8)
        self.assertTrue(result["available"])
        commands = {json.loads(call.args[0]) for call in connection.send.call_args_list}
        self.assertEqual(commands, dsp.READ_COMMANDS)

    def test_non_running_states_clear_levels(self):
        for state in ("Paused", "Inactive", "Starting", "Stalled"):
            with self.subTest(state=state):
                result = dsp.read_sample(fake_connection(replies(state)))
                self.assertEqual(result["state"], state)
                self.assertEqual(result["output_rms_db"], [-60, -60])
                self.assertEqual(result["output_peak_db"], [-60, -60])

    def test_silence_empty_and_clamped_levels(self):
        self.assertEqual(dsp.stereo_levels([]), [-60, -60])
        self.assertEqual(dsp.stereo_levels([None, -math.inf]), [-60, -60])
        self.assertEqual(dsp.stereo_levels([-1000, 3]), [-60, 0])

    def test_bad_levels_are_rejected(self):
        for levels in (None, {}, [True], ["-20"], [math.nan], [math.inf]):
            with self.subTest(levels=levels):
                with self.assertRaises(ValueError):
                    dsp.stereo_levels(levels)

    def test_invalid_responses_are_rejected(self):
        for reply in ("broken", "null", "[]", '{}',
                      '{"GetState":{"result":"Error"}}',
                      '{"GetState":{"result":"Ok"}}'):
            with self.subTest(reply=reply):
                connection = MagicMock()
                connection.recv.return_value = reply
                with self.assertRaises(ValueError):
                    dsp.read_command(connection, "GetState")

    def test_mutating_commands_are_never_sent(self):
        connection = MagicMock()
        with self.assertRaises(ValueError):
            dsp.read_command(connection, "SetVolume")
        connection.send.assert_not_called()

    def test_invalid_status_values_are_rejected(self):
        for key, value in (("GetState", "Unexpected"), ("GetState", []), ("GetClippedSamples", -1),
                           ("GetProcessingLoad", math.nan)):
            with self.subTest(key=key):
                values = replies()
                values[key] = value
                with self.assertRaises(ValueError):
                    dsp.read_sample(fake_connection(values))


class TestDspMonitor(unittest.TestCase):
    def test_starts_unavailable_and_expires_old_readings(self):
        monitor = dsp.DspMonitor("ws://unused:1234")
        self.assertFalse(monitor.snapshot()["available"])
        with patch("dsp.time.monotonic", return_value=100):
            monitor.publish(dsp.read_sample(fake_connection(replies())))
        with patch("dsp.time.monotonic", return_value=101):
            self.assertTrue(monitor.snapshot()["available"])
        with patch("dsp.time.monotonic", return_value=103):
            stale = monitor.snapshot()
            self.assertFalse(stale["available"])
            self.assertEqual(stale["output_rms_db"], [-60, -60])

    def test_snapshot_is_an_independent_copy(self):
        monitor = dsp.DspMonitor("ws://unused:1234")
        monitor.publish(dsp.read_sample(fake_connection(replies())))
        sample = monitor.snapshot()
        sample["output_rms_db"][0] = 0
        self.assertEqual(monitor.snapshot()["output_rms_db"], [-24, -18])

    def test_failed_connection_clears_readings(self):
        monitor = dsp.DspMonitor("ws://unused:1234")
        monitor.publish(dsp.read_sample(fake_connection(replies())))
        stop = MagicMock()
        stop.is_set.side_effect = [False, True]
        with patch("dsp.connect", side_effect=OSError("connection refused")):
            monitor.run(stop)
        self.assertFalse(monitor.snapshot()["available"])
        stop.wait.assert_called_once_with(dsp.RECONNECT_SECONDS)

    def test_reconnects_after_connection_failure(self):
        monitor = dsp.DspMonitor("ws://unused:1234")
        stop = MagicMock()
        stop.is_set.side_effect = [False, False, False, True, True]
        context = MagicMock()
        context.__enter__.return_value = fake_connection(replies())
        with patch("dsp.connect", side_effect=[OSError("offline"), context]) as connect:
            monitor.run(stop)
        self.assertEqual(connect.call_count, 2)
        self.assertIn(unittest.mock.call(dsp.RECONNECT_SECONDS), stop.wait.call_args_list)
        self.assertIn(unittest.mock.call(dsp.POLL_SECONDS), stop.wait.call_args_list)

    def test_api_returns_snapshot(self):
        with patch.object(main.dsp_monitor, "snapshot", return_value=dsp.unavailable()):
            self.assertEqual(main.dsp_status(), dsp.unavailable())

    def test_real_websocket_transport_uses_one_connection(self):
        # A local fake DSP verifies wire JSON and connection reuse, without a Pi.
        commands = []
        connections = []
        def handler(connection):
            connections.append(connection)
            for message in connection:
                command = json.loads(message)
                commands.append(command)
                connection.send(json.dumps({command: {
                    "result": "Ok", "value": replies()[command],
                }}))

        with serve(handler, "127.0.0.1", 0) as server:
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            port = server.socket.getsockname()[1]
            from websockets.sync.client import connect
            with connect(f"ws://127.0.0.1:{port}", proxy=None) as connection:
                first = dsp.read_sample(connection)
                second = dsp.read_sample(connection)
            server.shutdown()
            worker.join(timeout=2)

        self.assertEqual(first, second)
        self.assertEqual(len(connections), 1)
        self.assertEqual(len(commands), 8)
        self.assertTrue(set(commands).issubset(dsp.READ_COMMANDS))
