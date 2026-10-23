# Apple Music Bridge

A lightweight Now Playing dashboard for Apple Music streamed over AirPlay to a Raspberry Pi running moOde.

FastAPI reads metadata and artwork from moOde over SSH, while Shairport Sync supplies playback timing through D-Bus.

## Features

- Track title, artist and album
- Album artwork with a blurred background
- Playing and paused status
- Track duration and playback progress
- Source and output audio formats
- Fullscreen display
- Retains the last track information during temporary connection failures
- Runs in the background as a systemd user service

The dashboard displays playback information. Playback is controlled through the source device or moOde.

## How it works

1. The browser requests metadata from the FastAPI backend.
2. The backend reads moOde's `/var/local/www/aplmeta.json` over SSH.
3. Album artwork is retrieved from moOde's artwork cache.
4. Shairport Sync's D-Bus properties provide playback timing.
5. The browser animates progress between updates.

The backend requests one-second frame-position updates from Shairport Sync and reapplies that runtime setting every 30 seconds to handle possible Shairport restarts.

## Project structure

```text
apple-music-bridge/
├── main.py
└── static/
    ├── index.html
    ├── styles.css
    └── player.js
```

- `main.py`: FastAPI routes, SSH access, metadata and timing
- `static/index.html`: dashboard structure
- `static/styles.css`: dashboard styling
- `static/player.js`: API requests, display updates and progress animation

## Requirements

On the computer running the bridge:

- Python 3 with virtual environment support
- SSH client
- Network access to the Raspberry Pi
- Passwordless SSH access to the Pi

On the Raspberry Pi:

- moOde with its AirPlay renderer enabled
- Shairport Sync with the D-Bus interfaces used by the bridge
- AirPlay metadata available at `/var/local/www/aplmeta.json`

## Installation

```bash
git clone https://github.com/StephenC-ahh/apple-music-bridge.git
cd apple-music-bridge

python3 -m venv .venv
source .venv/bin/activate

python -m pip install -r requirements.txt
```

## Configuration

Connection settings are currently defined in `main.py`:

```python
MPD_HOST = "raspberrypi.local"
MPD_PORT = 6600

PI_SSH_HOST = "pi@raspberrypi.local"
```

Change these if your Pi uses a different hostname or username.

Confirm SSH works without an interactive password prompt:

```bash
ssh -o BatchMode=yes pi@raspberrypi.local \
  'cat /var/local/www/aplmeta.json'
```

If needed, set up an SSH key and connect manually once to verify the Pi's host key before starting the service.

## Run manually

From the project directory:

```bash
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8765
```

Open:

```text
http://127.0.0.1:8765/dashboard
```

The application must run with the project directory as its working directory so it can locate the static files.

## Run with systemd

The bridge runs as a user service, so service commands use `--user` without `sudo`.

Create the service directory:

```bash
mkdir -p ~/.config/systemd/user
```

Create or edit the unit:

```bash
nano ~/.config/systemd/user/apple-music-bridge.service
```

Example configuration — replace both project paths with your actual absolute path:

```ini
[Unit]
Description=Apple Music Bridge

[Service]
Type=simple
WorkingDirectory=/home/YOUR_USER/apple-music-bridge
ExecStart=/home/YOUR_USER/apple-music-bridge/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8765
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Load the unit and enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now apple-music-bridge.service
```

By default, an enabled user service starts when your user manager starts, normally at login.

To start it at boot and keep it running after logout, optionally enable lingering:

```bash
sudo loginctl enable-linger "$USER"
```

### Manage the service

Restart:

```bash
systemctl --user restart apple-music-bridge.service
```

Check status:

```bash
systemctl --user status apple-music-bridge.service --no-pager
```

Follow logs:

```bash
journalctl --user -u apple-music-bridge.service -f
```

Stop:

```bash
systemctl --user stop apple-music-bridge.service
```

Disable automatic startup and stop:

```bash
systemctl --user disable --now apple-music-bridge.service
```

## Apply updates

After committing or saving local changes:

```bash
git pull --ff-only
systemctl --user restart apple-music-bridge.service
```

Hard-refresh the dashboard with `Ctrl+Shift+R` after frontend changes.

Run `systemctl --user daemon-reload` before restarting only when the service unit itself has changed.

## API endpoints

| Endpoint | Purpose |
| --- | --- |
| `/` | Application name, version and status |
| `/dashboard` | Now Playing dashboard |
| `/api/airplay` | AirPlay metadata and playback state |
| `/api/artwork` | Current album artwork |
| `/api/progress` | Playback timing and progress |
| `/api/player` | MPD status |
| `/docs` | Interactive API documentation |

## Troubleshooting

### Service fails to start

Check the logs:

```bash
journalctl --user -u apple-music-bridge.service -n 50 --no-pager
```

Confirm that `WorkingDirectory` and `ExecStart` point to the correct project directory and virtual environment.

### Dashboard loads without styling or updates

Check that these files exist:

```text
static/styles.css
static/player.js
```

Restart the service and hard-refresh the browser.

### Track information is unavailable

Check SSH access and moOde's metadata:

```bash
ssh -o BatchMode=yes pi@raspberrypi.local \
  'cat /var/local/www/aplmeta.json'
```

Start AirPlay playback and check the service logs.

### Track information works but progress is unavailable

Progress also requires Shairport Sync's D-Bus timing properties. Check the service logs for frame-update warnings and confirm that Shairport Sync is running.

## Current scope

This is a personal prototype for a local setup.

- Connection settings are currently hardcoded.
- AirPlay metadata is cached for five seconds.
- The dashboard polls every three seconds.
- The backend does not provide playback-control endpoints.
- A populated metadata file does not guarantee that an AirPlay session is still active.
- The server binds to localhost in the examples above and has no application authentication.