// Keep the original tab icon as a fallback when album artwork is absent.
const favicon = document.querySelector('link[rel~="icon"]');
const defaultFavicon = favicon?.href;

function updateFavicon(artworkUrl) {
  if (favicon) {
    favicon.href = artworkUrl || defaultFavicon;
  }
}

    function formatDuration(milliseconds) {
      const value = Number(milliseconds);

      if (!Number.isFinite(value) || value < 0) {
        return 'Unknown';
      }

      const seconds = Math.floor(value / 1000);

      return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    }

    function setPlaybackStatus(state) {
      const playback = document.getElementById('playback');

      const labels = {
        playing: 'Playing',
        paused: 'Paused',
        disconnected: 'Disconnected',
        unavailable: 'Unavailable',
        unknown: 'Unknown'
      };

      playback.textContent = labels[state] ?? 'Unknown';

      playback.className = 'playback-status ' + (
        labels[state] ? state : 'unknown'
      );

      document.getElementById('player-shell').dataset.state = state;
    }

    let currentTrackKey = null;
    let playbackState = 'unknown';
    let progressPositionMs = null;
    let progressDurationMs = 0;
    let progressClockMs = null;
    let progressTargetMs = null;
    let updating = false;

    function clearProgress() {
      progressPositionMs = null;
      progressDurationMs = 0;
      progressClockMs = null;
      progressTargetMs = null;

      document.getElementById('progress-section').style.display =
        'none';

      document.getElementById('progress-fill').style.width = '0%';

      document.getElementById('elapsed-time').textContent = '0:00';
      document.getElementById('total-time').textContent = '0:00';

      document.getElementById('progress-track')
        .setAttribute('aria-valuenow', '0');
    }

    function drawProgress() {
      if (
        progressPositionMs === null ||
        progressDurationMs <= 0
      ) {
        return;
      }

      const elapsed = Math.max(
        0,
        Math.min(progressPositionMs, progressDurationMs)
      );

      const percent = (elapsed / progressDurationMs) * 100;

      document.getElementById('progress-section').style.display =
        'block';

      document.getElementById('progress-fill').style.width =
        `${percent}%`;

      document.getElementById('elapsed-time').textContent =
        formatDuration(elapsed);

      document.getElementById('total-time').textContent =
        formatDuration(progressDurationMs);

      document.getElementById('progress-track')
        .setAttribute('aria-valuenow', percent.toFixed(1));
    }

    function animateProgress(now) {
      if (progressClockMs !== null &&
          progressPositionMs !== null &&
          progressDurationMs > 0 &&
          playbackState === 'playing') {
        // Advance with the browser's monotonic clock at exactly 1x.
        // Server samples never accelerate or decelerate the bar.
        const delta = Math.max(0, now - progressClockMs);
        progressPositionMs = Math.min(
          progressDurationMs,
          progressPositionMs + delta
        );
        drawProgress();
      }

      progressClockMs = now;
      requestAnimationFrame(animateProgress);
    }

    function freezeProgress() {
      progressClockMs = null;
    }

    function clearTrack() {
      updateFavicon(null);
      const artwork = document.getElementById('artwork');

      artwork.style.display = 'none';
      artwork.removeAttribute('src');
      delete artwork.dataset.cover;

      document.getElementById('backdrop').style.backgroundImage =
        'none';

      document.getElementById('track').textContent =
        'No AirPlay connection';

      document.getElementById('artist').textContent = '';
      document.getElementById('album').textContent = '';

      document.getElementById('duration').textContent = 'Unknown';
      document.getElementById('source-format').textContent = 'Unknown';
      document.getElementById('output-format').textContent = 'Unknown';

      clearProgress();
    }

    async function updatePlayer() {
      if (updating) {
        return;
      }

      updating = true;

      try {
        const response = await fetch('/api/airplay', {
          cache: 'no-store'
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const player = await response.json();

        // Metadata may be cached. Progress state takes priority
        // once a valid timing sample has arrived.
        if (progressPositionMs === null) {
          playbackState = player.state;
          setPlaybackStatus(playbackState);
        }

        if (!player.connected) {
          // Retain artwork, track information and progress.
          playbackState = player.state === 'unavailable'
            ? 'unavailable'
            : 'disconnected';

          setPlaybackStatus(playbackState);
          freezeProgress();

          if (currentTrackKey === null) {
            document.getElementById('track').textContent =
              'No AirPlay connection';
          }

          return;
        }

        const trackKey = JSON.stringify([
          player.title,
          player.artist,
          player.album,
          player.duration_ms,
          player.cover_url
        ]);

        if (trackKey !== currentTrackKey) {
          currentTrackKey = trackKey;
          clearProgress();
        }

        const artwork = document.getElementById('artwork');

        if (player.cover_url) {
          if (artwork.dataset.cover !== player.cover_url) {
            artwork.src =
              `/api/artwork?cover=${encodeURIComponent(player.cover_url)}`;

            artwork.dataset.cover = player.cover_url;
            updateFavicon(artwork.src);

            document.getElementById('backdrop').style.backgroundImage =
              `url("${artwork.src}")`;
          }

          artwork.style.display = 'block';
        } else {
          updateFavicon(null);
          artwork.style.display = 'none';
          artwork.removeAttribute('src');
          delete artwork.dataset.cover;

          document.getElementById('backdrop').style.backgroundImage =
            'none';
        }

        document.getElementById('track').textContent =
          player.title ?? 'No track information';

        document.getElementById('artist').textContent =
          player.artist ?? '';

        document.getElementById('album').textContent =
          player.album ?? '';

        document.getElementById('duration').textContent =
          formatDuration(player.duration_ms);

        document.getElementById('source-format').textContent =
          player.source_format ?? 'Unknown';

        document.getElementById('output-format').textContent =
          player.output_format ?? 'Unknown';

        if (
          player.state === 'playing' ||
          player.state === 'paused'
        ) {
          try {
            const progressResponse = await fetch('/api/progress', {
              cache: 'no-store'
            });

            if (!progressResponse.ok) {
              throw new Error(`HTTP ${progressResponse.status}`);
            }

            const progress = await progressResponse.json();

            if (
              progress.available &&
              Number(progress.duration_ms) ===
                Number(player.duration_ms)
            ) {
              playbackState = progress.state;
              setPlaybackStatus(playbackState);

              const elapsed = Number(progress.elapsed_ms);
              const duration = Number(progress.duration_ms);

              if (
                Number.isFinite(elapsed) &&
                Number.isFinite(duration) &&
                duration > 0
              ) {
                const target = Math.max(
                  0,
                  Math.min(elapsed, duration)
                );

                progressDurationMs = duration;
                // Use the server to initialise a track, to freeze on pause,
                // or to recognise a genuine seek. Routine polls do not
                // alter the running browser clock or its speed.
                if (progressPositionMs === null || playbackState === 'paused') {
                  progressPositionMs = target;
                } else if (Math.abs(target - progressPositionMs) > 8000) {
                  // A large discrepancy is probably a seek or a stale
                  // stream reset, not ordinary network jitter.
                  progressPositionMs = target;
                }

                if (progressClockMs === null) {
                  progressClockMs = performance.now();
                }
                drawProgress();
              } else {
                playbackState = 'unknown';
                setPlaybackStatus(playbackState);
                freezeProgress();
              }
            } else {
              playbackState = 'unknown';
              setPlaybackStatus(playbackState);
              freezeProgress();
            }
          } catch (error) {
            playbackState = 'unknown';
            setPlaybackStatus(playbackState);
            freezeProgress();

            console.error('Progress update failed:', error);
          }
        } else {
          playbackState = player.state;
          setPlaybackStatus(playbackState);
          freezeProgress();
        }
      } catch (error) {
        playbackState = 'unavailable';
        setPlaybackStatus(playbackState);
        freezeProgress();

        if (currentTrackKey === null) {
          document.getElementById('track').textContent =
            'Cannot reach Apple Music Bridge';
        }

        console.error('AirPlay update failed:', error);
      } finally {
        updating = false;
      }
    }

    let updatingOutput = false;

    async function updateOutputDevice() {
      if (updatingOutput) return;
      updatingOutput = true;
      const label = document.getElementById('output-device');
      try {
        const response = await fetch('/api/output', { cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const output = await response.json();
        label.textContent = output.state === 'active' && output.devices.length
          ? output.devices.join(', ')
          : output.state === 'idle' ? 'No active output' : 'Unavailable';
      } catch (error) {
        label.textContent = 'Unavailable';
        console.error('Output device update failed:', error);
      } finally {
        updatingOutput = false;
      }
    }

    updateOutputDevice();
    setInterval(updateOutputDevice, 5000);

    requestAnimationFrame(animateProgress);
    updatePlayer();
    setInterval(updatePlayer, 3000);

    // v0.9: Full-screen control. Playback logic above is unchanged.
    const fullscreenButton = document.getElementById('fullscreen-button');

    fullscreenButton.addEventListener('click', async () => {
      try {
        if (document.fullscreenElement) {
          await document.exitFullscreen();
        } else {
          await document.documentElement.requestFullscreen();
        }
      } catch (error) {
        console.error('Full-screen request failed:', error);
      }
    });

    document.addEventListener('fullscreenchange', () => {
      const active = Boolean(document.fullscreenElement);
      fullscreenButton.textContent = active ? '⤢' : '⛶';
      fullscreenButton.setAttribute(
        'aria-label', active ? 'Exit full screen' : 'Enter full screen'
      );
      fullscreenButton.title = active ? 'Exit full screen' : 'Enter full screen';
    });



