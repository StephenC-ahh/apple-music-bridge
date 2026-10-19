
import json
import logging
import re
import subprocess
import time
from contextlib import asynccontextmanager
from threading import Event, Thread

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from mpd import MPDClient


VERSION = "0.8.0"

MPD_HOST = "raspberrypi.local"
MPD_PORT = 6600

PI_SSH_HOST = "pi@raspberrypi.local"

AIRPLAY_CACHE_TTL = 5
FRAME_UPDATE_INTERVAL = 1.0
FRAME_RECHECK_SECONDS = 30

logger = logging.getLogger("apple-music-bridge")

airplay_cache = {
    "data": None,
    "timestamp": 0,
}


def run_pi_command(command, *, text=True):
    """Run a command on the Pi without changing its configuration."""
    return subprocess.run(
        [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=3",
            PI_SSH_HOST,
            command,
        ],
        capture_output=True,
        text=text,
        timeout=5,
        check=True,
    )


def enable_frame_updates():
    """Request live frame-position updates from Shairport Sync."""
    command = (
        "busctl --system call "
        "org.gnome.ShairportSync "
        "/org/gnome/ShairportSync "
        "org.gnome.ShairportSync "
        "SetFramePositionUpdateInterval "
        f"d {FRAME_UPDATE_INTERVAL}"
    )

    try:
        run_pi_command(command)
        logger.info(
            "Shairport Sync frame updates enabled: %.1fs",
            FRAME_UPDATE_INTERVAL,
        )
        return True

    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning(
            "Could not initialise Shairport Sync frame updates: %s",
            exc,
        )
        return False


def maintain_frame_updates(stop_event):
    """Reapply the runtime setting after possible Shairport restarts."""
    while not stop_event.is_set():
        enable_frame_updates()
        stop_event.wait(FRAME_RECHECK_SECONDS)


@asynccontextmanager
async def lifespan(app):
    stop_event = Event()

    worker = Thread(
        target=maintain_frame_updates,
        args=(stop_event,),
        name="shairport-frame-updates",
        daemon=True,
    )
    worker.start()

    try:
        yield
    finally:
        stop_event.set()
        worker.join(timeout=6)


app = FastAPI(
    title="Apple Music Bridge",
    version=VERSION,
    lifespan=lifespan,
)


@app.get("/")
def home():
    return {
        "name": "Apple Music Bridge",
        "version": VERSION,
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

    if (
        airplay_cache["data"] is not None
        and now - airplay_cache["timestamp"] < AIRPLAY_CACHE_TTL
    ):
        return airplay_cache["data"]

    try:
        result = run_pi_command(
            "cat /var/local/www/aplmeta.json"
        )

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

        airplay_cache["data"] = data
        airplay_cache["timestamp"] = time.monotonic()

        return data

    except (subprocess.SubprocessError, ValueError, OSError) as exc:
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
        result = run_pi_command(
            f"cat {remote_path}",
            text=False,
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

    result = run_pi_command(script)

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

        # Shairport Sync uses wrapping 32-bit frame counters.
        frame_modulus = 2**32

        total_frames = (end - start) % frame_modulus
        elapsed_frames = (current - start) % frame_modulus

        if total_frames == 0:
            raise ValueError("Invalid track frame range")

        if elapsed_frames > total_frames:
            return {
                "available": False,
                "reason": "Frame position outside track range",
            }

        fraction = elapsed_frames / total_frames

        metadata = airplay()
        duration_ms = int(metadata.get("duration_ms") or 0)

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
