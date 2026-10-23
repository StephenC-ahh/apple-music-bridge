// Read-only CamillaDSP output meters: RMS bars and peak markers.
const METER_FLOOR_DB = -60;

function meterPercent(db) {
  return Math.max(
    0,
    Math.min(
      100,
      ((db - METER_FLOOR_DB) / -METER_FLOOR_DB) * 100
    )
  );
}

function parseMeterSample(data) {
  if (!data || data.available !== true) return null;

  const states = [
    'Running',
    'Paused',
    'Inactive',
    'Starting',
    'Stalled'
  ];

  if (!states.includes(data.state)) return null;

  const validLevels = values =>
    Array.isArray(values) &&
    values.length === 2 &&
    values.every(
      value =>
        typeof value === 'number' &&
        Number.isFinite(value)
    );

  if (
    !validLevels(data.output_rms_db) ||
    !validLevels(data.output_peak_db) ||
    !Number.isInteger(data.clipped_samples) ||
    data.clipped_samples < 0 ||
    typeof data.processing_load !== 'number' ||
    !Number.isFinite(data.processing_load) ||
    data.processing_load < 0
  ) {
    return null;
  }

  return {
    ...data,
    output_rms_db:
      data.state === 'Running'
        ? data.output_rms_db
        : [METER_FLOOR_DB, METER_FLOOR_DB],
    output_peak_db:
      data.state === 'Running'
        ? data.output_peak_db
        : [METER_FLOOR_DB, METER_FLOOR_DB]
  };
}

function getMeterElements(channel) {
  const meter = document.getElementById(`meter-${channel}`);

  return {
    meter,
    fill: meter.querySelector('.meter-fill'),
    peak: meter.querySelector('.meter-peak'),
    value: document.getElementById(`meter-${channel}-value`)
  };
}

function startMeters() {
  const status = document.getElementById('dsp-status');
  const clips = document.getElementById('dsp-clips');
  const load = document.getElementById('dsp-load');
  const channels = ['left', 'right'].map(getMeterElements);

  function draw(sample) {
    status.textContent = sample
      ? `DSP ${sample.state.toLowerCase()}`
      : 'DSP off / unavailable';

    clips.textContent = sample
      ? `Clipped samples: ${sample.clipped_samples}`
      : 'Clipped samples: —';

    clips.classList.toggle(
      'has-clips',
      Boolean(sample && sample.clipped_samples > 0)
    );

    load.textContent = sample
      ? `DSP load: ${sample.processing_load.toFixed(1)}%`
      : 'DSP load: —';

    channels.forEach((channel, index) => {
      const rms = sample
        ? sample.output_rms_db[index]
        : METER_FLOOR_DB;

      const peak = sample
        ? sample.output_peak_db[index]
        : METER_FLOOR_DB;

      channel.fill.style.clipPath =
        `inset(0 ${100 - meterPercent(rms)}% 0 0)`;

      channel.peak.style.left = `${meterPercent(peak)}%`;

      channel.peak.style.display =
        sample && sample.state === 'Running'
          ? 'block'
          : 'none';

      channel.value.textContent = sample
        ? rms <= METER_FLOOR_DB
          ? '≤−60'
          : rms.toFixed(1)
        : '—';

      channel.meter.setAttribute(
        'aria-valuenow',
        String(Math.max(METER_FLOOR_DB, Math.min(0, rms)))
      );

      channel.meter.setAttribute(
        'aria-valuetext',
        sample ? `${rms.toFixed(1)} dBFS` : 'Unavailable'
      );
    });
  }

  let lastReceived = null;
  let generation = 0;
  let controller = null;
  let pollTimer = null;

  async function poll() {
    const requestGeneration = generation;
    const requestController = new AbortController();

    controller = requestController;

    const timeout = setTimeout(
      () => requestController.abort(),
      1500
    );

    try {
      const response = await fetch('/api/dsp', {
        cache: 'no-store',
        signal: requestController.signal
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const sample = parseMeterSample(await response.json());

      if (
        requestGeneration !== generation ||
        document.hidden
      ) {
        return;
      }

      draw(sample);
      lastReceived = sample ? performance.now() : null;
    } catch (error) {
      if (requestGeneration === generation) {
        lastReceived = null;
        draw(null);
      }
    } finally {
      clearTimeout(timeout);

      if (
        requestGeneration === generation &&
        !document.hidden
      ) {
        pollTimer = setTimeout(poll, 200);
      }
    }
  }

  // Clear stale readings if the backend stops responding.
  setInterval(() => {
    if (
      lastReceived !== null &&
      performance.now() - lastReceived > 2000
    ) {
      draw(null);
      lastReceived = null;
    }
  }, 250);

  // Stop polling in hidden tabs and refresh when visible again.
  document.addEventListener('visibilitychange', () => {
    generation += 1;
    clearTimeout(pollTimer);
    controller?.abort();

    lastReceived = null;
    draw(null);

    if (!document.hidden) {
      poll();
    }
  });

  draw(null);

  if (!document.hidden) {
    poll();
  }
}

if (typeof document !== 'undefined') {
  startMeters();
}

if (typeof module !== 'undefined') {
  module.exports = { meterPercent, parseMeterSample };
}