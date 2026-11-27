/**
 * history.js — metric/range state and local chart data.
 *
 * History is lazy and cached by (metric, hours) in store.js. Live samples from
 * the latest datapoint snapshot are appended in the browser; this file never
 * fetches the API directly. Wrapped in an IIFE to keep window.* intact.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  let lastRevision = -1;

  function setText(id, value) {
    const el = $(id);
    if (!el) return;
    const next = value == null ? '' : String(value);
    if (el.textContent !== next) el.textContent = next;
  }

  const METRIC_META = {
    pm1_ug_m3: { label: 'PM1.0', unit: 'µg/m³', decimals: 1 },
    pm2_5_ug_m3: { label: 'PM2.5', unit: 'µg/m³', decimals: 1 },
    pm4_ug_m3: { label: 'PM4.0', unit: 'µg/m³', decimals: 1 },
    pm10_ug_m3: { label: 'PM10', unit: 'µg/m³', decimals: 1 },
    co2_ppm: { label: 'CO₂', unit: 'ppm', decimals: 0 },
    temperature_c: { label: 'Temperature', unit: '°C', decimals: 1 },
    relative_humidity_percent: { label: 'Humidity', unit: '%RH', decimals: 1 },
    voc_index: { label: 'VOC Index', unit: 'Index', decimals: 0 },
    nox_index: { label: 'NOx Index', unit: 'Index', decimals: 0 },
    tps_um: { label: 'Typical particle size', unit: 'µm', decimals: 2 },
    cpu_temperature_c: { label: 'Pi CPU temperature', unit: '°C', decimals: 1 }
  };

  window.HIST = {
    metric: 'pm2_5_ug_m3', hours: 24, samples: [], label: 'PM2.5', unit: 'µg/m³', decimals: 1,
    stats: null, loaded: false, nextCursor: null, lastLoad: 0, error: '', requestSeq: 0,
    viewStart: 0, viewEnd: 1,
    dragging: false, dragStartX: 0, dragEndX: 0,
    hoverIdx: -1
  };

  function computeStats(samples) {
    const values = (samples || [])
      .map(function (row) { return Number(row[1]); })
      .filter(function (value) { return Number.isFinite(value); });
    if (!values.length) return null;
    let total = 0;
    values.forEach(function (value) { total += value; });
    return {
      min: Math.min.apply(null, values),
      avg: total / values.length,
      max: Math.max.apply(null, values),
      count: values.length
    };
  }

  function updateHistoryControls() {
    const H = window.HIST;
    document.querySelectorAll('#metricSelector button').forEach(b => {
      b.classList.toggle('active', b.dataset.metric === H.metric);
    });
    document.querySelectorAll('#rangeSelector button').forEach(b => {
      b.classList.toggle('active', Number(b.dataset.hours) === H.hours);
    });
    const meta = METRIC_META[H.metric] || { label: H.metric, unit: '', decimals: 1 };
    H.label = meta.label; H.unit = meta.unit; H.decimals = meta.decimals;
    const rangeLabel = H.hours <= 1 ? H.hours + ' hour' :
      H.hours < 24 ? H.hours + ' hours' : Math.round(H.hours / 24) + ' days';
    setText('historyTitle', H.label + ' — last ' + rangeLabel);
  }

  function updateHistoryStats() {
    const H = window.HIST;
    const s = H.stats;
    if (!s) {
      setText('hmin', '--'); setText('havg', '--'); setText('hmax', '--');
      return;
    }
    setText('hmin', window.fmtVal(s.min, H.decimals));
    setText('havg', window.fmtVal(s.avg, H.decimals));
    setText('hmax', window.fmtVal(s.max, H.decimals));
    setText('hminunit', H.unit);
    setText('havgunit', H.unit);
    setText('hmaxunit', H.unit);
  }

  function resetHistory() {
    const H = window.HIST;
    H.requestSeq++;
    H.samples = [];
    H.stats = null;
    H.loaded = false;
    H.nextCursor = null;
    H.error = '';
    H.viewStart = 0;
    H.viewEnd = 1;
    updateHistoryControls();
    updateHistoryStats();
    window.drawHistory();
  }

  async function loadHistory() {
    const H = window.HIST;
    H.requestSeq++;
    const seq = H.requestSeq;
    setText('historyMeta', 'Loading history...');
    try {
      const entry = await window.DP.history(H.metric, H.hours);
      if (seq !== H.requestSeq) return;
      H.samples = (entry && entry.samples) || [];
      H.stats = computeStats(H.samples);
      H.nextCursor = entry ? entry.next_cursor : null;
      H.loaded = true;
      H.lastLoad = Date.now();
      H.error = '';
      H.viewStart = 0;
      H.viewEnd = 1;
      updateHistoryStats();
      window.drawHistory();
      setText('historyMeta', H.samples.length + ' samples · ' + H.hours + 'h range');
    } catch (e) {
      if (seq !== H.requestSeq) return;
      H.loaded = false;
      H.error = e.message;
      setText('historyMeta', 'History unavailable: ' + e.message);
      window.drawHistory();
    }
  }

  function latestDatapoint(payload, metric) {
    const points = (payload && (payload.datapoints || payload.live)) || [];
    for (let i = 0; i < points.length; i++) {
      if (points[i].metric === metric) return points[i];
    }
    return null;
  }

  function appendLiveSample(state) {
    if (!state || !state.latest || state.revision === lastRevision) return;
    lastRevision = state.revision;
    const H = window.HIST;
    if (!H.loaded) return;
    const point = latestDatapoint(state.latest, H.metric);
    if (!point || point.observed_at == null || point.value == null) return;
    const epoch = Date.parse(point.observed_at) / 1000;
    if (!Number.isFinite(epoch)) return;
    if (epoch < Date.now() / 1000 - H.hours * 3600) return;
    const entry = window.DP.mergeHistory(H.metric, H.hours, [[epoch, point.value]]);
    if (!entry) return;
    H.samples = entry.samples || [];
    H.stats = computeStats(H.samples);
    H.nextCursor = entry.next_cursor;
    updateHistoryStats();
    setText('historyMeta', H.samples.length + ' samples · ' + H.hours + 'h range · live');
    window.drawHistory();
  }

  function selectMetric(metric) {
    window.HIST.metric = metric;
    resetHistory();
    loadHistory();
  }

  function selectRange(hours) {
    window.HIST.hours = Number(hours);
    resetHistory();
    loadHistory();
  }

  window.updateHistoryControls = updateHistoryControls;
  window.updateHistoryStats = updateHistoryStats;
  window.loadHistory = loadHistory;
  window.selectMetric = selectMetric;
  window.selectRange = selectRange;

  if (window.DP) window.DP.subscribe(appendLiveSample);
})();
