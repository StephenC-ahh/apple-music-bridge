
import json
import re
import subprocess
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from mpd import MPDClient


app = FastAPI(title="Apple Music Bridge", version="0.4.0")

MPD_HOST = "raspberrypi.local"
MPD_PORT = 6600

AIRPLAY_CACHE_TTL = 5

airplay_cache = {
    "data": None,
    "timestamp": 0,
}


@app.get("/")
def home():
    return {
        "name": "Apple Music Bridge",
        "version": "0.4.0",
        "status": "running",
    }


def get_playback_state(playstate):
    """Translate moOde's last AirPlay playback event."""
    if playstate == "Resume":
        return "playing"
    if playstate == "Pause":
        return "paused"
    return "unknown"


@app.get("/api/airplay")
def airplay():
    now = time.monotonic()

    # Return fresh cached metadata
    if (
        airplay_cache["data"] is not None
        and now - airplay_cache["timestamp"] < AIRPLAY_CACHE_TTL
    ):
        return airplay_cache["data"]

    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=3",
                "pi@raspberrypi.local",
                "cat /var/local/www/aplmeta.json",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )

        # An empty file means AirPlay has disconnected
        if not result.stdout.strip():
            data = {
                "renderer": "AirPlay",
                "connected": False,
                "state": "disconnected",
                "playstate": None,
                "title": None,
                "artist": None,
                "album": None,
                "duration_ms": None,
                "cover_url": None,
                "source_format": None,
                "output_format": None,
                "source": "moode-airplay",
            }
        else:
            metadata = json.loads(result.stdout)

            playstate = metadata.get("playstate")

            data = {
                "renderer": "AirPlay",
                "connected": True,
                "state": get_playback_state(playstate),
                "playstate": playstate,
                "title": metadata.get("title"),
                "artist": metadata.get("artist"),
                "album": metadata.get("album"),
                "duration_ms": metadata.get("duration"),
                "cover_url": metadata.get("cover_url"),
                "source_format": metadata.get("sformat"),
                "output_format": metadata.get("oformat"),
                "source": "moode-airplay",
            }

        # Cache both connected and disconnected states
        airplay_cache["data"] = data
        airplay_cache["timestamp"] = time.monotonic()

        return data

    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        # Do not mistake an SSH or JSON error for disconnection
        return {
            "renderer": "AirPlay",
            "connected": False,
            "state": "unavailable",
            "playstate": None,
            "error": str(exc),
            "source": "moode-airplay",
        }


@app.get("/api/artwork")
def artwork():
    # Reuse the AirPlay metadata cache
    player = airplay()

    if not player.get("connected"):
        raise HTTPException(
            status_code=503,
            detail="AirPlay metadata unavailable",
        )

    cover_url = player.get("cover_url")

    if not cover_url:
        raise HTTPException(
            status_code=404,
            detail="No album artwork",
        )

    # Accept only moOde AirPlay artwork filenames
    if not re.fullmatch(
        r"imagesw/airplay-covers/cover-[a-zA-Z0-9_-]+\.jpg",
        cover_url,
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid artwork path",
        )

    remote_path = f"/var/local/www/{cover_url}"

    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=3",
                "pi@raspberrypi.local",
                f"cat {remote_path}",
            ],
            capture_output=True,
            timeout=5,
            check=True,
        )

        return Response(
            content=result.stdout,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=60"},
        )

    except (subprocess.SubprocessError, OSError):
        raise HTTPException(
            status_code=502,
            detail="Could not retrieve album artwork",
        )


@app.get("/api/player")
def player():
    client = MPDClient()
    client.timeout = 3

    try:
        client.connect(MPD_HOST, MPD_PORT)

        status = client.status()
        song = client.currentsong()
        state = status.get("state", "unknown")

        return {
            "renderer": "moOde MPD",
            "connected": True,
            "state": state,
            "track": (
                (song.get("title") or song.get("file"))
                if state == "play" else None
            ),
            "artist": (
                song.get("artist")
                if state == "play" else None
            ),
            "volume": status.get("volume"),
            "source": "mpd",
        }

    except Exception as exc:
        return {
            "renderer": "moOde MPD",
            "connected": False,
            "state": "unavailable",
            "track": None,
            "error": str(exc),
            "source": "mpd",
        }

    finally:
        try:
            client.disconnect()
        except Exception:
            pass


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse("static/index.html")


def read_airplay_timing():
    """Read Shairport Sync timing properties over SSH."""
    script = """
busctl --system get-property \
  org.gnome.ShairportSync \
  /org/gnome/ShairportSync \
  org.gnome.ShairportSync.RemoteControl ProgressString

busctl --system get-property \
  org.gnome.ShairportSync \
  /org/gnome/ShairportSync \
  org.gnome.ShairportSync FramePosition
"""

    result = subprocess.run(
        [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=3",
            "pi@raspberrypi.local",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )

    values = re.findall(r's "([^"]*)"', result.stdout)

    if len(values) != 2:
        raise ValueError("Missing Shairport Sync timing properties")

    progress_parts = values[0].split("/")
    frame_parts = values[1].split("/")

    if len(progress_parts) != 3 or len(frame_parts) != 2:
        raise ValueError("Invalid Shairport Sync timing format")

    start, reported_position, end = map(int, progress_parts)
    current, playback_timestamp = map(int, frame_parts)

    return start, current, end


@app.get("/api/progress")
def progress():
    try:
        start, current, end = read_airplay_timing()

        if end <= start:
            raise ValueError("Invalid track frame range")

        # The current frame must belong to the reported track.
        if not start <= current <= end:
            return {
                "available": False,
                "reason": "Frame position outside track range",
            }

        total_frames = end - start
        elapsed_frames = current - start
        fraction = elapsed_frames / total_frames

        metadata = airplay()

        # moOde supplies duration as a string, e.g. "200265".
        duration_ms = int(metadata.get("duration_ms") or 0)

        # Allow progress for both playing and paused tracks.
        # Shairport Sync freezes FramePosition when paused.
        if (
            not metadata.get("connected")
            or metadata.get("state") not in ("playing", "paused")
        ):
            return {
                "available": False,
                "reason": "AirPlay is not connected",
            }

        if duration_ms <= 0:
            return {
                "available": False,
                "reason": "Track duration unavailable",
            }

        elapsed_ms = round(duration_ms * fraction)

        return {
            "available": True,
            "state": metadata["state"],
            "elapsed_ms": elapsed_ms,
            "duration_ms": duration_ms,
            "percent": round(fraction * 100, 1),
        }

    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        return {
            "available": False,
            "reason": str(exc),
        }
