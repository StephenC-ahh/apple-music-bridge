
import json
import subprocess
import time

from fastapi import HTTPException
from fastapi.responses import Response
from fastapi import FastAPI
from fastapi.responses import FileResponse
from mpd import MPDClient

app = FastAPI(title="Apple Music Bridge", version="0.3.0")

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
        "version": "0.2.0",
        "status": "running",
    }

@app.get("/api/airplay")
def airplay():
    now = time.monotonic()

    # Return cached metadata if it's still fresh
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

        metadata = json.loads(result.stdout)

        data = {
            "renderer": "AirPlay",
            "connected": True,
            "title": metadata.get("title"),
            "artist": metadata.get("artist"),
            "album": metadata.get("album"),
            "duration_ms": metadata.get("duration"),
            "cover_url": metadata.get("cover_url"),
            "source_format": metadata.get("sformat"),
            "output_format": metadata.get("oformat"),
            "playstate": metadata.get("playstate"),
            "source": "moode-airplay",
        }

        # Store the result in the cache
        airplay_cache["data"] = data
        airplay_cache["timestamp"] = time.monotonic()

        return data

    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        return {
            "renderer": "AirPlay",
            "connected": False,
            "error": str(exc),
            "source": "moode-airplay",
        }


@app.get("/api/artwork")
def artwork():
    # Reuse the metadata endpoint and its 5-second cache
    player = airplay()

    if not player.get("connected"):
        raise HTTPException(status_code=503, detail="AirPlay metadata unavailable")

    cover_url = player.get("cover_url")

    if not cover_url:
        raise HTTPException(status_code=404, detail="No album artwork")

    # Only accept moOde's expected artwork filename format
    import re

    if not re.fullmatch(
        r"imagesw/airplay-covers/cover-[a-zA-Z0-9_-]+\.jpg",
        cover_url,
    ):
        raise HTTPException(status_code=400, detail="Invalid artwork path")

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
                song.get("title") or song.get("file")
                if state == "play" else None
            ),
            "artist": song.get("artist") if state == "play" else None,
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
