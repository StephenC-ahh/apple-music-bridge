"""Read-only CamillaDSP metering over one persistent WebSocket connection."""

import json
import logging
import math
import time
from copy import deepcopy
from threading import Lock

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect


FLOOR_DB = -60.0
POLL_SECONDS = 0.2
STALE_SECONDS = 2.0
RECONNECT_SECONDS = 3.0
logger = logging.getLogger("apple-music-bridge.dsp")
READ_COMMANDS = frozenset({
    "GetState", "GetSignalLevelsSinceLast", "GetClippedSamples", "GetProcessingLoad",
})


def unavailable():
    return {
        "available": False,
        "state": "Unavailable",
        "output_rms_db": [FLOOR_DB, FLOOR_DB],
        "output_peak_db": [FLOOR_DB, FLOOR_DB],
        "clipped_samples": None,
        "processing_load": None,
    }


def read_command(connection, command):
    if command not in READ_COMMANDS:
        raise ValueError("Unsupported DSP read command")
    connection.send(json.dumps(command))
    reply = json.loads(connection.recv(timeout=1))
    result = reply.get(command) if isinstance(reply, dict) else None
    if not isinstance(result, dict) or result.get("result") != "Ok":
        raise ValueError(f"Invalid CamillaDSP reply for {command}")
    if "value" not in result:
        raise ValueError(f"Missing CamillaDSP value for {command}")
    return result["value"]


def stereo_levels(values):
    """Clamp display levels; empty samples and null silence become the floor."""
    if not isinstance(values, list):
        raise ValueError("Invalid DSP level array")
    levels = []
    for value in values[:2]:
        if value is None:
            value = FLOOR_DB
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Invalid DSP signal level")
        if math.isnan(value) or value == math.inf:
            raise ValueError("Invalid DSP signal level")
        levels.append(max(FLOOR_DB, min(0.0, float(value))))
    return (levels + [FLOOR_DB, FLOOR_DB])[:2]


def read_sample(connection):
    state = read_command(connection, "GetState")
    if not isinstance(state, str) or state not in {
        "Running", "Paused", "Inactive", "Starting", "Stalled"
    }:
        raise ValueError("Unknown CamillaDSP state")
    levels = read_command(connection, "GetSignalLevelsSinceLast")
    if not isinstance(levels, dict):
        raise ValueError("Invalid DSP levels reply")
    rms = stereo_levels(levels.get("playback_rms"))
    peak = stereo_levels(levels.get("playback_peak"))
    clipped = read_command(connection, "GetClippedSamples")
    load = read_command(connection, "GetProcessingLoad")
    if type(clipped) is not int or clipped < 0:
        raise ValueError("Invalid clipped sample count")
    if (isinstance(load, bool) or not isinstance(load, (int, float))
            or not math.isfinite(load) or load < 0):
        raise ValueError("Invalid DSP load")
    return {
        "available": True,
        "state": state,
        "output_rms_db": rms if state == "Running" else [FLOOR_DB, FLOOR_DB],
        "output_peak_db": peak if state == "Running" else [FLOOR_DB, FLOOR_DB],
        "clipped_samples": clipped,
        "processing_load": round(load, 1),
    }


class DspMonitor:
    def __init__(self, url):
        self.url = url
        self._lock = Lock()
        self._data = unavailable()
        self._updated = None

    def publish(self, data):
        with self._lock:
            self._data = deepcopy(data)
            self._updated = time.monotonic()

    def snapshot(self):
        with self._lock:
            if self._updated is None or time.monotonic() - self._updated > STALE_SECONDS:
                return unavailable()
            return deepcopy(self._data)

    def run(self, stop_event):
        while not stop_event.is_set():
            try:
                with connect(
                    self.url, open_timeout=2, close_timeout=1,
                    max_size=65536, proxy=None,
                ) as connection:
                    while not stop_event.is_set():
                        self.publish(read_sample(connection))
                        stop_event.wait(POLL_SECONDS)
            except (OSError, TimeoutError, ValueError, WebSocketException) as exc:
                self.publish(unavailable())
                logger.info("CamillaDSP metering unavailable: %s", exc)
                stop_event.wait(RECONNECT_SECONDS)
        self.publish(unavailable())
