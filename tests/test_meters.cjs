const test = require('node:test');
const assert = require('node:assert/strict');
const { meterPercent, parseMeterSample } = require('../static/meters.js');

const running = {
  available: true, state: 'Running', output_rms_db: [-24, -18],
  output_peak_db: [-12, -6], clipped_samples: 0, processing_load: 0.8
};

test('dBFS values map to bounded display widths', () => {
  assert.equal(meterPercent(-60), 0);
  assert.equal(meterPercent(-30), 50);
  assert.equal(meterPercent(0), 100);
  assert.equal(meterPercent(-1000), 0);
  assert.equal(meterPercent(5), 100);
});

test('running samples retain the measured levels', () => {
  assert.deepEqual(parseMeterSample(running), running);
});

test('paused or stalled DSP clears levels without losing status', () => {
  for (const state of ['Paused', 'Inactive', 'Starting', 'Stalled']) {
    const sample = parseMeterSample({ ...running, state });
    assert.deepEqual(sample.output_rms_db, [-60, -60]);
    assert.deepEqual(sample.output_peak_db, [-60, -60]);
    assert.equal(sample.state, state);
  }
});

test('unavailable or malformed samples are rejected', () => {
  for (const data of [null, {}, { ...running, available: false },
    { ...running, output_rms_db: [NaN, -10] },
    { ...running, output_peak_db: [] }, { ...running, state: 'bogus' },
    { ...running, clipped_samples: -1 }, { ...running, processing_load: null }]) {
    assert.equal(parseMeterSample(data), null);
  }
});

test('browser display clears on disconnect and recovers on reconnect', async () => {
  const vm = require('node:vm');
  const fs = require('node:fs');
  const timers = new Map();
  const intervals = [];
  const listeners = {};
  const elements = new Map();
  let nextTimer = 0;
  let nextSample = running;
  let clock = 100;
  function element(id) {
    if (!elements.has(id)) {
      const parts = new Map();
      elements.set(id, {
        textContent: '', style: {}, attributes: {},
        classList: { toggle() {} },
        setAttribute(name, value) { this.attributes[name] = value; },
        querySelector(name) {
          if (!parts.has(name)) parts.set(name, { style: {} });
          return parts.get(name);
        }
      });
    }
    return elements.get(id);
  }
  const document = {
    hidden: false,
    getElementById: element,
    addEventListener: (event, handler) => { listeners[event] = handler; }
  };
  vm.runInNewContext(fs.readFileSync(require.resolve('../static/meters.js'), 'utf8'), {
    document, AbortController, console,
    performance: { now: () => clock },
    fetch: async () => ({ ok: true, json: async () => nextSample }),
    setTimeout: (callback, delay) => {
      const id = ++nextTimer;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout: id => timers.delete(id),
    setInterval: callback => intervals.push(callback)
  });
  const flush = () => new Promise(resolve => setImmediate(resolve));
  const poll = async () => {
    const [id, timer] = [...timers.entries()].find(([, value]) => value.delay === 200);
    timers.delete(id);
    timer.callback();
    await flush();
  };
  await flush();
  assert.equal(element('dsp-status').textContent, 'DSP running');
  assert.equal(element('meter-left-value').textContent, '-24.0');
  assert.equal(element('meter-left').attributes['aria-valuenow'], '-24');
  nextSample = { available: false };
  await poll();
  assert.equal(element('meter-left-value').textContent, '—');
  assert.equal(element('meter-left').attributes['aria-valuenow'], '-60');
  assert.equal(element('meter-left').querySelector('.meter-peak').style.display, 'none');
  nextSample = running;
  await poll();
  assert.equal(element('meter-left-value').textContent, '-24.0');
  clock += 2100;
  intervals[0]();
  assert.equal(element('meter-left-value').textContent, '—');
  document.hidden = true;
  listeners.visibilitychange();
  assert.equal(timers.size, 0);
  document.hidden = false;
  listeners.visibilitychange();
  await flush();
  assert.equal(element('meter-left-value').textContent, '-24.0');
  assert.equal([...timers.values()].filter(timer => timer.delay === 200).length, 1);
});
